//! Native canonical edge sorting. The run wire format matches streaming.py.
use std::cmp::Reverse;
use std::collections::BinaryHeap;
use std::fs::{File, OpenOptions};
use std::io::{BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU8, Ordering};

use sha2::{Digest, Sha256};

use crate::encoded_direct::{
    cancellable_sort_unstable_by, DirectEdge, KernelError, STATE_CANCELLED,
};

const MAGIC: &[u8; 15] = b"PYOWL2VEC-RUN\x00\x01";
const HEADER: usize = 63;

fn resource(message: impl Into<String>) -> KernelError {
    KernelError::Resource(message.into())
}
fn io(error: std::io::Error) -> KernelError {
    resource(format!("native canonical temporary I/O failed: {error}"))
}
fn check(state: &AtomicU8) -> Result<(), KernelError> {
    if state.load(Ordering::Acquire) == STATE_CANCELLED {
        Err(KernelError::Cancelled)
    } else {
        Ok(())
    }
}
fn add(a: usize, b: usize) -> Result<usize, KernelError> {
    a.checked_add(b)
        .ok_or_else(|| resource("native canonical byte counter overflow"))
}
fn edge_bytes(edge: &DirectEdge) -> Result<usize, KernelError> {
    add(
        std::mem::size_of::<DirectEdge>(),
        add(
            edge.source.capacity(),
            add(edge.relation.capacity(), edge.destination.capacity())?,
        )?,
    )
}
fn clone_edge(edge: &DirectEdge) -> Result<DirectEdge, KernelError> {
    fn text(value: &str) -> Result<String, KernelError> {
        let mut output = String::new();
        output
            .try_reserve_exact(value.len())
            .map_err(|_| resource("native canonical edge allocation failed"))?;
        output.push_str(value);
        Ok(output)
    }
    Ok(DirectEdge {
        source: text(&edge.source)?,
        relation: text(&edge.relation)?,
        destination: text(&edge.destination)?,
    })
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Limits {
    pub edges: usize,
    pub bytes: usize,
    pub fan_in: usize,
    pub max_open_files: usize,
    pub spill: usize,
    pub temporary: usize,
}
#[derive(Clone, Debug)]
struct Run {
    path: PathBuf,
    count: usize,
    payload: usize,
}
impl Run {
    fn size(&self) -> usize {
        HEADER + self.payload
    }
}

struct Reader {
    file: BufReader<File>,
    remaining: usize,
    payload_remaining: usize,
    digest: Sha256,
    expected: [u8; 32],
    verified: bool,
}
impl Reader {
    fn open(run: &Run, capacity: usize) -> Result<Self, KernelError> {
        let mut file = File::open(&run.path).map_err(io)?;
        let mut header = [0u8; HEADER];
        file.read_exact(&mut header).map_err(io)?;
        let count = u64::from_be_bytes(header[15..23].try_into().unwrap());
        let payload = u64::from_be_bytes(header[23..31].try_into().unwrap());
        let expected: [u8; 32] = header[31..].try_into().unwrap();
        if &header[..15] != MAGIC
            || count != run.count as u64
            || payload != run.payload as u64
            || expected == [0; 32]
            || file.metadata().map_err(io)?.len() != run.size() as u64
        {
            return Err(resource("native canonical corrupt run header"));
        }
        Ok(Self {
            file: BufReader::with_capacity(capacity, file),
            remaining: run.count,
            payload_remaining: run.payload,
            digest: Sha256::new(),
            expected,
            verified: false,
        })
    }
    fn next(&mut self, maximum: usize) -> Result<Option<DirectEdge>, KernelError> {
        if self.remaining == 0 {
            if !self.verified {
                let mut tail = [0];
                if self.payload_remaining != 0
                    || self.file.read(&mut tail).map_err(io)? != 0
                    || self.digest.clone().finalize().as_slice() != self.expected
                {
                    return Err(resource("native canonical corrupt run checksum"));
                }
                self.verified = true;
            }
            return Ok(None);
        }
        let mut lengths = [0u8; 12];
        self.file.read_exact(&mut lengths).map_err(io)?;
        let sizes: [usize; 3] = std::array::from_fn(|i| {
            u32::from_be_bytes(lengths[i * 4..i * 4 + 4].try_into().unwrap()) as usize
        });
        let record = add(12, add(sizes[0], add(sizes[1], sizes[2])?)?)?;
        if record > self.payload_remaining
            || add(record, std::mem::size_of::<DirectEdge>())? > maximum
        {
            return Err(resource("native canonical corrupt or oversized run record"));
        }
        self.digest.update(lengths);
        let mut fields = Vec::new();
        fields
            .try_reserve_exact(3)
            .map_err(|_| resource("native canonical field allocation failed"))?;
        for size in sizes {
            let mut bytes = Vec::new();
            bytes
                .try_reserve_exact(size)
                .map_err(|_| resource("native canonical field allocation failed"))?;
            bytes.resize(size, 0);
            self.file.read_exact(&mut bytes).map_err(io)?;
            self.digest.update(&bytes);
            fields.push(
                String::from_utf8(bytes)
                    .map_err(|_| resource("native canonical invalid UTF-8 run"))?,
            );
        }
        self.remaining -= 1;
        self.payload_remaining -= record;
        let destination = fields.pop().unwrap();
        let relation = fields.pop().unwrap();
        let source = fields.pop().unwrap();
        Ok(Some(DirectEdge {
            source,
            relation,
            destination,
        }))
    }
}

struct Merge {
    readers: Vec<Reader>,
    heap: BinaryHeap<Reverse<(DirectEdge, usize)>>,
    bytes: usize,
    maximum: usize,
}
impl Merge {
    fn new(runs: &[Run], maximum: usize) -> Result<Self, KernelError> {
        let mut result = Self {
            readers: Vec::new(),
            heap: BinaryHeap::new(),
            bytes: 0,
            maximum,
        };
        result
            .readers
            .try_reserve_exact(runs.len())
            .map_err(|_| resource("native canonical reader allocation failed"))?;
        result
            .heap
            .try_reserve_exact(runs.len())
            .map_err(|_| resource("native canonical heap allocation failed"))?;
        result.bytes = add(
            result.readers.capacity() * std::mem::size_of::<Reader>(),
            result.heap.capacity() * std::mem::size_of::<Reverse<(DirectEdge, usize)>>(),
        )?;
        let io_capacity = 4096.min(maximum / runs.len().max(1) / 4);
        result.bytes = add(result.bytes, io_capacity * runs.len())?;
        if result.bytes > maximum {
            return Err(resource("native canonical reader workspace limit exceeded"));
        }
        for (index, run) in runs.iter().enumerate() {
            let mut reader = Reader::open(run, io_capacity)?;
            if let Some(edge) = reader.next(maximum - result.bytes)? {
                result.bytes = add(result.bytes, edge_bytes(&edge)?)?;
                if result.bytes > maximum {
                    return Err(resource("native canonical merge workspace limit exceeded"));
                }
                result.heap.push(Reverse((edge, index)));
            }
            result.readers.push(reader);
        }
        Ok(result)
    }
    fn next(&mut self, state: &AtomicU8) -> Result<Option<DirectEdge>, KernelError> {
        check(state)?;
        let Some(Reverse((edge, index))) = self.heap.pop() else {
            return Ok(None);
        };
        self.bytes -= edge_bytes(&edge)?;
        if let Some(next) = self.readers[index].next(self.maximum - self.bytes)? {
            self.bytes = add(self.bytes, edge_bytes(&next)?)?;
            if self.bytes > self.maximum {
                return Err(resource("native canonical merge workspace limit exceeded"));
            }
            self.heap.push(Reverse((next, index)));
        }
        Ok(Some(edge))
    }
}

pub(crate) struct CanonicalSpool {
    directory: PathBuf,
    limits: Limits,
    unique: bool,
    buffer: Vec<DirectEdge>,
    strings: usize,
    runs: Vec<Run>,
    files: Vec<PathBuf>,
    memory: std::vec::IntoIter<DirectEdge>,
    merge: Option<Merge>,
    pending: Vec<DirectEdge>,
    last: Option<DirectEdge>,
    finished: bool,
    raw: usize,
    distinct: usize,
    emitted: usize,
    sequence: usize,
    live: usize,
    peak_live: usize,
    total_spill: usize,
    run_count: usize,
    merge_passes: usize,
    peak_memory: usize,
    sorts: usize,
    queued: Option<DirectEdge>,
    metadata_bytes: usize,
}
impl CanonicalSpool {
    pub(crate) fn new(
        directory: PathBuf,
        limits: Limits,
        unique: bool,
    ) -> Result<Self, KernelError> {
        if !directory.is_dir()
            || limits.edges == 0
            || limits.bytes == 0
            || limits.fan_in < 2
            || limits.fan_in + 1 > limits.max_open_files
        {
            return Err(resource(
                "native canonical configuration or temporary directory invalid",
            ));
        }
        Ok(Self {
            directory,
            limits,
            unique,
            buffer: Vec::new(),
            strings: 0,
            runs: Vec::new(),
            files: Vec::new(),
            memory: Vec::new().into_iter(),
            merge: None,
            pending: Vec::new(),
            last: None,
            finished: false,
            raw: 0,
            distinct: 0,
            emitted: 0,
            sequence: 0,
            live: 0,
            peak_live: 0,
            total_spill: 0,
            run_count: 0,
            merge_passes: 0,
            peak_memory: 0,
            sorts: 0,
            queued: None,
            metadata_bytes: 0,
        })
    }
    fn grow_disk(&mut self, amount: usize) -> Result<(), KernelError> {
        let live = add(self.live, amount)?;
        let total = add(self.total_spill, amount)?;
        if live > self.limits.temporary {
            return Err(resource("native canonical max_temporary_bytes exceeded"));
        }
        if total > self.limits.spill {
            return Err(resource("native canonical max_spill_bytes exceeded"));
        }
        self.live = live;
        self.total_spill = total;
        self.peak_live = self.peak_live.max(live);
        Ok(())
    }
    fn reserve_edge(&mut self, edge: &DirectEdge) -> Result<(), KernelError> {
        let strings = add(
            self.strings,
            edge_bytes(edge)? - std::mem::size_of::<DirectEdge>(),
        )?;
        if self.buffer.len() == self.buffer.capacity() {
            let capacity = self
                .buffer
                .capacity()
                .max(1)
                .saturating_mul(2)
                .min(self.limits.edges);
            let maximum = add(
                strings,
                add(
                    capacity * std::mem::size_of::<DirectEdge>(),
                    self.buffer.capacity() * std::mem::size_of::<DirectEdge>(),
                )?,
            )?;
            if maximum > self.limits.bytes / 3 {
                return Err(resource(
                    "native canonical buffer allocation peak exceeds native_buffer_bytes",
                ));
            }
            self.peak_memory = self.peak_memory.max(maximum);
            self.buffer
                .try_reserve_exact(capacity - self.buffer.len())
                .map_err(|_| resource("native canonical buffer allocation failed"))?;
        }
        self.strings = strings;
        let retained = add(
            strings,
            self.buffer.capacity() * std::mem::size_of::<DirectEdge>(),
        )?;
        if retained > self.limits.bytes / 3 {
            return Err(resource(
                "native canonical buffer exceeds native_buffer_bytes",
            ));
        }
        self.peak_memory = self.peak_memory.max(retained);
        Ok(())
    }
    pub(crate) fn push(&mut self, edge: DirectEdge, state: &AtomicU8) -> Result<(), KernelError> {
        check(state)?;
        if self.finished {
            return Err(resource("native canonical input already finished"));
        }
        let size = edge_bytes(&edge)?;
        if size > self.limits.bytes / 16 {
            return Err(resource(
                "native canonical edge exceeds native_buffer_bytes",
            ));
        }
        let predicted = add(
            add(self.strings, size)?,
            self.buffer.capacity() * std::mem::size_of::<DirectEdge>(),
        )?;
        if !self.buffer.is_empty()
            && (self.buffer.len() >= self.limits.edges || predicted > self.limits.bytes / 6)
        {
            self.flush(state)?;
        }
        self.reserve_edge(&edge)?;
        self.buffer.push(edge);
        self.raw = add(self.raw, 1)?;
        Ok(())
    }
    fn sort_buffer(&mut self, state: &AtomicU8) -> Result<(), KernelError> {
        cancellable_sort_unstable_by(&mut self.buffer, state, Ord::cmp)?;
        self.sorts = add(self.sorts, 1)?;
        if self.unique {
            self.buffer.dedup();
        }
        Ok(())
    }
    fn write_run<F>(&mut self, mut next: F, state: &AtomicU8) -> Result<Run, KernelError>
    where
        F: FnMut() -> Result<Option<DirectEdge>, KernelError>,
    {
        self.sequence = add(self.sequence, 1)?;
        let path = self
            .directory
            .join(format!("native-run-{}.bin", self.sequence));
        let mut options = OpenOptions::new();
        options.read(true).write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let metadata = add(512, path.as_os_str().len() * 8)?;
        let reserved = add(self.metadata_bytes, metadata)?;
        if reserved > self.limits.bytes / 12 {
            return Err(resource(
                "native canonical run metadata exceeds native_buffer_bytes",
            ));
        }
        self.metadata_bytes = reserved;
        self.peak_memory = self
            .peak_memory
            .max(add(self.limits.bytes * 3 / 4, reserved)?);
        self.files
            .try_reserve(1)
            .map_err(|_| resource("native canonical run metadata allocation failed"))?;
        self.files.push(path.clone());
        let mut file = BufWriter::with_capacity(
            4096.min(self.limits.bytes / 32),
            options.open(&path).map_err(io)?,
        );
        self.grow_disk(HEADER)?;
        file.write_all(&[0; HEADER]).map_err(io)?;
        let mut digest = Sha256::new();
        let mut count = 0usize;
        let mut payload = 0usize;
        let mut last: Option<DirectEdge> = None;
        while let Some(edge) = next()? {
            check(state)?;
            if self.unique && last.as_ref() == Some(&edge) {
                continue;
            }
            let fields = [
                edge.source.as_bytes(),
                edge.relation.as_bytes(),
                edge.destination.as_bytes(),
            ];
            let mut lengths = [0; 12];
            let mut amount = 12;
            for (index, field) in fields.iter().enumerate() {
                let size = u32::try_from(field.len()).map_err(|_| {
                    resource("native canonical edge field exceeds run-format bound")
                })?;
                lengths[index * 4..index * 4 + 4].copy_from_slice(&size.to_be_bytes());
                amount = add(amount, field.len())?;
            }
            self.grow_disk(amount)?;
            file.write_all(&lengths).map_err(io)?;
            digest.update(lengths);
            for field in fields {
                file.write_all(field).map_err(io)?;
                digest.update(field);
            }
            payload = add(payload, amount)?;
            count = add(count, 1)?;
            if self.unique {
                last = Some(edge);
            }
        }
        check(state)?;
        file.seek(SeekFrom::Start(0)).map_err(io)?;
        file.write_all(MAGIC).map_err(io)?;
        file.write_all(&(count as u64).to_be_bytes()).map_err(io)?;
        file.write_all(&(payload as u64).to_be_bytes())
            .map_err(io)?;
        file.write_all(&digest.finalize()).map_err(io)?;
        file.flush().map_err(io)?;
        self.run_count = add(self.run_count, 1)?;
        Ok(Run {
            path,
            count,
            payload,
        })
    }
    fn flush(&mut self, state: &AtomicU8) -> Result<(), KernelError> {
        self.sort_buffer(state)?;
        let mut edges = std::mem::take(&mut self.buffer).into_iter();
        self.strings = 0;
        let run = self.write_run(|| Ok(edges.next()), state)?;
        self.runs
            .try_reserve(1)
            .map_err(|_| resource("native canonical run metadata allocation failed"))?;
        self.runs.push(run);
        Ok(())
    }
    pub(crate) fn finish(&mut self, state: &AtomicU8) -> Result<(), KernelError> {
        if self.finished {
            return Err(resource("native canonical finish repeated"));
        }
        check(state)?;
        if self.runs.is_empty() {
            self.sort_buffer(state)?;
            self.memory = std::mem::take(&mut self.buffer).into_iter();
            self.strings = 0;
        } else {
            if !self.buffer.is_empty() {
                self.flush(state)?;
            }
            let mut runs = std::mem::take(&mut self.runs);
            while runs.len() > self.limits.fan_in {
                self.merge_passes = add(self.merge_passes, 1)?;
                let mut following = Vec::new();
                for group in runs.chunks(self.limits.fan_in) {
                    if group.len() == 1 {
                        following.push(group[0].clone());
                        continue;
                    }
                    let mut merge = Merge::new(group, self.limits.bytes / 3)?;
                    self.peak_memory = self.peak_memory.max(merge.bytes);
                    let run = self.write_run(|| merge.next(state), state)?;
                    drop(merge);
                    for old in group {
                        self.delete(old)?;
                    }
                    following.push(run);
                }
                runs = following;
            }
            self.merge = Some(Merge::new(&runs, self.limits.bytes / 3)?);
            self.peak_memory = self.peak_memory.max(self.merge.as_ref().unwrap().bytes);
            self.runs = runs;
        }
        self.finished = true;
        Ok(())
    }
    fn delete(&mut self, run: &Run) -> Result<(), KernelError> {
        std::fs::remove_file(&run.path).map_err(io)?;
        self.live -= run.size();
        Ok(())
    }
    pub(crate) fn prepare_page(
        &mut self,
        amount: usize,
        state: &AtomicU8,
    ) -> Result<&[DirectEdge], KernelError> {
        if !self.finished {
            return Err(resource("native canonical output not prepared"));
        }
        if !self.pending.is_empty() {
            return Ok(&self.pending);
        }
        if self.queued.is_none()
            && self
                .merge
                .as_ref()
                .map_or(self.memory.len() == 0, |merge| merge.heap.is_empty())
        {
            return Ok(&self.pending);
        }
        let maximum = amount
            .min(self.raw.saturating_sub(self.emitted))
            .min(self.limits.edges)
            .min(self.limits.bytes / 12 / std::mem::size_of::<DirectEdge>());
        if maximum == 0 {
            return Err(resource("native canonical output workspace too small"));
        }
        self.pending
            .try_reserve_exact(maximum)
            .map_err(|_| resource("native canonical output allocation failed"))?;
        let mut page_bytes = self.pending.capacity() * std::mem::size_of::<DirectEdge>();
        while self.pending.len() < maximum {
            check(state)?;
            let next = if self.queued.is_some() {
                self.queued.take()
            } else {
                match &mut self.merge {
                    Some(merge) => merge.next(state)?,
                    None => self.memory.next(),
                }
            };
            let Some(edge) = next else { break };
            let size = edge_bytes(&edge)?;
            if size > self.limits.bytes / 16 {
                return Err(resource(
                    "native canonical output edge exceeds native_buffer_bytes",
                ));
            }
            if !self.pending.is_empty() && add(page_bytes, size)? > self.limits.bytes / 4 {
                self.queued = Some(edge);
                break;
            }
            let distinct = self.last.as_ref() != Some(&edge);
            if distinct {
                self.distinct = add(self.distinct, 1)?;
            }
            self.last = Some(clone_edge(&edge)?);
            if !self.unique || distinct {
                page_bytes = add(page_bytes, size)?;
                self.pending.push(edge);
            }
        }
        // Reserve independent bounded phases: sort/merge, final page, current/last row,
        // and metadata. Python's requested final objects are interface allocations.
        self.peak_memory = self
            .peak_memory
            .max(add(self.limits.bytes * 3 / 4, self.metadata_bytes)?);
        Ok(&self.pending)
    }
    pub(crate) fn commit_page(&mut self) {
        self.emitted += self.pending.len();
        self.pending = Vec::new();
    }
    pub(crate) fn metrics(&self) -> [usize; 9] {
        [
            self.raw,
            self.distinct,
            self.emitted,
            self.run_count,
            self.merge_passes,
            self.peak_live,
            self.total_spill,
            self.peak_memory,
            self.sorts,
        ]
    }
}
impl Drop for CanonicalSpool {
    fn drop(&mut self) {
        self.merge = None;
        for path in &self.files {
            let _ = std::fs::remove_file(path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;
    static NEXT: AtomicUsize = AtomicUsize::new(0);
    fn workspace() -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "projector-canonical-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&p).unwrap();
        p
    }
    fn edge(source: &str) -> DirectEdge {
        DirectEdge {
            source: source.into(),
            relation: "r".into(),
            destination: "é".into(),
        }
    }
    fn limits(edges: usize) -> Limits {
        Limits {
            edges,
            bytes: 1024 * 1024,
            fan_in: 2,
            max_open_files: 3,
            spill: usize::MAX,
            temporary: usize::MAX,
        }
    }
    #[test]
    fn canonical_preserve_unique_and_spill_match_exact_bytes() {
        for count in [1, 2, 7, 100] {
            for unique in [false, true] {
                let path = workspace();
                let state = AtomicU8::new(0);
                let mut sorter = CanonicalSpool::new(path.clone(), limits(count), unique).unwrap();
                let input = ["z", "é", "a", "a", "e\u{301}", "Z", "é"];
                for text in input {
                    sorter.push(edge(text), &state).unwrap();
                }
                sorter.finish(&state).unwrap();
                let mut actual = Vec::new();
                loop {
                    let page = sorter.prepare_page(2, &state).unwrap();
                    if page.is_empty() {
                        break;
                    }
                    actual.extend_from_slice(page);
                    sorter.commit_page();
                }
                let mut expected: Vec<_> = input.into_iter().map(edge).collect();
                expected.sort();
                if unique {
                    expected.dedup();
                }
                assert_eq!(actual, expected);
                assert_eq!(sorter.metrics()[0], 7);
                assert_eq!(sorter.metrics()[1], 5);
                if count == 100 {
                    assert_eq!(sorter.metrics()[3], 0);
                }
                drop(sorter);
                assert_eq!(std::fs::read_dir(&path).unwrap().count(), 0);
                std::fs::remove_dir(path).unwrap();
            }
        }
    }
    #[test]
    fn cancelled_and_limited_spills_clean_up() {
        for cancel in [false, true] {
            let path = workspace();
            let state = AtomicU8::new(0);
            let mut bound = limits(1);
            bound.spill = 0;
            let mut sorter = CanonicalSpool::new(path.clone(), bound, false).unwrap();
            sorter.push(edge("a"), &state).unwrap();
            if cancel {
                state.store(STATE_CANCELLED, Ordering::Release);
            }
            assert!(sorter.push(edge("b"), &state).is_err());
            drop(sorter);
            assert_eq!(std::fs::read_dir(&path).unwrap().count(), 0);
            std::fs::remove_dir(path).unwrap();
        }
    }
    #[test]
    fn pending_page_is_retained_until_publication_commits() {
        let path = workspace();
        let state = AtomicU8::new(0);
        let mut sorter = CanonicalSpool::new(path.clone(), limits(10), false).unwrap();
        for text in ["b", "a"] {
            sorter.push(edge(text), &state).unwrap();
        }
        sorter.finish(&state).unwrap();
        let first = sorter.prepare_page(1, &state).unwrap().to_vec();
        assert_eq!(sorter.prepare_page(1, &state).unwrap(), first);
        assert_eq!(sorter.metrics()[2], 0);
        sorter.commit_page();
        assert_eq!(sorter.prepare_page(1, &state).unwrap(), [edge("b")]);
        drop(sorter);
        std::fs::remove_dir(path).unwrap();
    }
    #[test]
    fn corrupt_truncated_and_invalid_utf8_runs_are_rejected() {
        for corrupt in [0, 1, 2] {
            let path = workspace();
            let state = AtomicU8::new(0);
            let mut sorter = CanonicalSpool::new(path.clone(), limits(1), false).unwrap();
            sorter.push(edge("a"), &state).unwrap();
            sorter.push(edge("b"), &state).unwrap();
            let run = &sorter.runs[0];
            let mut bytes = std::fs::read(&run.path).unwrap();
            match corrupt {
                0 => {
                    bytes[31] ^= 1;
                }
                1 => {
                    bytes.pop();
                }
                _ => {
                    bytes[HEADER + 12] = 0xff;
                }
            }
            std::fs::write(&run.path, bytes).unwrap();
            let result = sorter.finish(&state).and_then(|_| {
                while !sorter.prepare_page(1, &state)?.is_empty() {
                    sorter.commit_page();
                }
                Ok(())
            });
            assert!(result.is_err());
            drop(sorter);
            std::fs::remove_dir(path).unwrap();
        }
    }
}
