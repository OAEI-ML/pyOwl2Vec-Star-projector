package org.oaei_ml.oracle

import java.io.{BufferedWriter, File, FileOutputStream, OutputStreamWriter, PrintWriter}
import java.nio.charset.StandardCharsets
import java.util.Base64

import scala.collection.JavaConverters._

import org.mowl.Projectors.OWL2VecStarProjector
import org.semanticweb.owlapi.apibinding.OWLManager
import org.semanticweb.owlapi.model.{IRI, MissingImportEvent, MissingImportListener, OWLOntology}
import org.semanticweb.owlapi.model.parameters.Imports
import org.semanticweb.owlapi.util.AutoIRIMapper

/** Thin transport around the exact, externally staged mOWL source.
  *
  * The line protocol base64-encodes every uncontrolled value, allowing the Python
  * regeneration driver to preserve arbitrary Unicode and control characters.
  */
object OracleRunner {
  private val encoder = Base64.getEncoder

  private def b64(value: String): String =
    encoder.encodeToString(value.getBytes(StandardCharsets.UTF_8))

  private def emit(writer: PrintWriter, fields: String*): Unit = {
    writer.println(fields.mkString("\t"))
    writer.flush()
  }

  private final case class Config(
      fixtureRoot: File,
      output: File,
      inputs: List[File],
      bidirectional: Boolean,
      onlyTaxonomy: Boolean,
      includeLiterals: Boolean
  )

  private def parseBoolean(name: String, value: String): Boolean = value match {
    case "true"  => true
    case "false" => false
    case _        => throw new IllegalArgumentException(s"$name must be true or false")
  }

  private def parse(args: Array[String]): Config = {
    val values = args.grouped(2).map {
      case Array(key, value) => key -> value
      case _ => throw new IllegalArgumentException("arguments must be --name value pairs")
    }.toMap
    val inputs = values.getOrElse("--inputs", sys.error("--inputs is required"))
      .split(File.pathSeparator).toList.map(new File(_).getCanonicalFile)
    Config(
      new File(values.getOrElse("--fixture-root", sys.error("--fixture-root is required"))).getCanonicalFile,
      new File(values.getOrElse("--output", sys.error("--output is required"))).getCanonicalFile,
      inputs,
      parseBoolean("--bidirectional", values.getOrElse("--bidirectional", "false")),
      parseBoolean("--only-taxonomy", values.getOrElse("--only-taxonomy", "false")),
      parseBoolean("--include-literals", values.getOrElse("--include-literals", "false"))
    )
  }

  private def ontologyIdentity(ontology: OWLOntology): String = {
    val id = ontology.getOntologyID
    if (id.getOntologyIRI.isPresent) id.getOntologyIRI.get.toString else ""
  }

  def main(args: Array[String]): Unit = {
    val config = parse(args)
    config.output.getParentFile.mkdirs()
    val writer = new PrintWriter(new BufferedWriter(new OutputStreamWriter(
      new FileOutputStream(config.output), StandardCharsets.UTF_8)))
    try {
      emit(writer, "PROTOCOL", "mowl-projector-oracle/1")
      emit(writer, "FLAGS", config.bidirectional.toString, config.onlyTaxonomy.toString,
        config.includeLiterals.toString)

      // Projector creation is deliberately outside the input loop. Multiple inputs
      // reproduce the mutable Scala-instance lifecycle defect; one input is fresh-instance.
      val projector = new OWL2VecStarProjector(
        config.bidirectional, config.onlyTaxonomy, config.includeLiterals)
      config.inputs.zipWithIndex.foreach { case (input, invocation) =>
        val manager = OWLManager.createOWLOntologyManager()
        manager.getIRIMappers.add(new AutoIRIMapper(config.fixtureRoot, true))
        manager.addMissingImportListener(new MissingImportListener {
          override def importMissing(event: MissingImportEvent): Unit =
            emit(writer, "MISSING_IMPORT", invocation.toString,
              b64(event.getImportedOntologyURI.toString),
              b64(Option(event.getCreationException).map(_.getClass.getName).getOrElse("")))
        })
        emit(writer, "BEGIN", invocation.toString, b64(input.getName))
        try {
          val ontology = manager.loadOntologyFromOntologyDocument(input)
          ontology.getImportsClosure.asScala.toList
            .sortBy(ontologyIdentity)
            .foreach { loaded =>
              val document = manager.getOntologyDocumentIRI(loaded).toString
              emit(writer, "DOCUMENT", invocation.toString, b64(ontologyIdentity(loaded)), b64(document))
            }
          val result = projector.project(ontology).asScala.toList
          result.foreach(edge => emit(writer, "EDGE", invocation.toString,
            b64(edge.src), b64(edge.rel), b64(edge.dst)))
          emit(writer, "SUCCESS", invocation.toString, result.size.toString)
        } catch {
          case throwable: Throwable =>
            emit(writer, "ERROR", invocation.toString, b64(throwable.getClass.getName),
              b64(Option(throwable.getMessage).getOrElse("")))
        } finally {
          emit(writer, "END", invocation.toString)
        }
      }
    } finally {
      writer.close()
    }
  }
}
