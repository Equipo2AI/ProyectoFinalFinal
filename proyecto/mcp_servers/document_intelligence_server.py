"""Servidor MCP de imágenes y documentos.

Expone OCR, descripción de imágenes y conversión de documentos a Markdown
para que el orquestador principal pueda usar el agente sin cargarlo
directamente.
"""

from fastmcp import FastMCP

from proyecto.agents.document_intelligence_agent import (
    describe_an_image,
    document_to_markdown,
    ocr_image,
)

mcp = FastMCP("Document Intelligence MCP")


@mcp.tool()
def extraer_texto_imagen(image_path: str) -> str:
    """Extrae el texto visible de una imagen local (factura, recibo, comprobante)."""
    return ocr_image.invoke({"image_path": image_path})


@mcp.tool()
def describir_imagen(image_path: str) -> str:
    """Describe una imagen local y resume sus datos visibles si es un comprobante."""
    return describe_an_image.invoke({"image_path": image_path})


@mcp.tool()
def convertir_documento_markdown(file_path: str) -> str:
    """Convierte un documento local (PDF, DOCX, XLSX, PPTX) a Markdown."""
    return document_to_markdown.invoke({"file_path": file_path})


@mcp.tool()
def validar_tipo_documental(texto: str = "", documento_id: str = "") -> dict:
    """Compara texto extraido con referencias locales usando embeddings, sin LLM."""
    from proyecto.agents.document_similarity import evaluate
    from proyecto.security.document_access import extract_authorized, authorize_file
    try:
        # El texto del cliente no acredita ni acceso ni procedencia.
        _, _, safe_text, _, _ = extract_authorized(documento_id)
        result = evaluate(safe_text)
        authorize_file(documento_id)
        return result
    except PermissionError:
        return {"decision": "error_tecnico", "code": "ACCESO_DENEGADO", "message": "Documento no autorizado."}
    except Exception:
        return {"decision": "error_tecnico", "code": "ERROR_TECNICO", "message": "No se pudo validar el documento."}


@mcp.tool()
def preparar_documento_consultable(file_path: str, texto: str) -> dict:
    """Revalida y fragmenta el adjunto; devuelve contexto temporal autorizado."""
    try:
        return _preparar_documento_consultable(file_path, texto)
    except PermissionError:
        return {"status": "acceso_denegado", "code": "ACCESO_DENEGADO", "evidence": [], "message": "Acceso denegado para preparar este documento."}
    except Exception:
        return {"status": "error_tecnico", "code": "ERROR_TECNICO", "evidence": [], "message": "No se pudo preparar el documento por un fallo técnico."}


def _preparar_documento_consultable(file_path: str, texto: str) -> dict:
    """Fragmenta el adjunto aceptado; retorna contexto temporal, sin persistirlo."""
    from proyecto.security.local_auth import principal
    from proyecto.agents.document_questions import prepare_context, session_owner
    principal()
    from proyecto.security.document_access import extract_authorized, bind_context, authorize_file
    from proyecto.agents.document_similarity import evaluate
    from proyecto.agents.document_questions import admission_failure
    # Comprobar el archivo real tambien en MCP: no confiar en una bandera
    # de admision ni en texto que un cliente pudiera sustituir.
    identifier, _, actual, pages, incomplete = extract_authorized(file_path)
    admission = evaluate(actual)
    authorize_file(identifier)
    failure = admission_failure(admission)
    if failure:
        return failure
    from proyecto.agents.document_questions import validate_integrity, document_metadata
    integrity = validate_integrity(pages, incomplete)
    authorize_file(identifier)
    context = prepare_context(identifier)
    context['integrity'] = integrity
    context['metadata'] = document_metadata(context['pages'])
    context["owner"] = session_owner()
    context["admission"] = admission
    bind_context(identifier, context)
    return context


@mcp.tool()
def responder_pregunta_documental(pregunta: str, documento: dict, anterior: str = "") -> dict:
    """Recupera evidencia del adjunto de la sesion y responde con citas verificadas."""
    from proyecto.security.local_auth import authorize_request
    from proyecto.agents.document_questions import answer, session_owner
    from proyecto.security.document_access import authorize_context
    try:
        authorize_request(pregunta)
        if anterior:
            authorize_request(anterior)
        if documento.get("owner") != session_owner():
            raise PermissionError("El adjunto pertenece a otra sesión o alcance.")
        authorize_context(documento)
        result = answer(pregunta, documento, anterior)
        authorize_context(documento)
        authorize_request(pregunta)
        return result
    except PermissionError:
        return {"status": "acceso_denegado", "code": "ACCESO_DENEGADO", "evidence": [], "message": "Acceso denegado para consultar este documento o estos datos."}
    except Exception:
        return {"status": "error_tecnico", "code": "ERROR_TECNICO", "evidence": [], "message": "No se pudo completar la consulta documental por un fallo técnico."}


if __name__ == "__main__":
    mcp.run()
