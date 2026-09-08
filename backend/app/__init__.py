"""Inicialización de la aplicación."""

# pypdf puede devolver el carácter de reemplazo (�) en determinados PDF
# oficiales cuya tabla de codificación no se interpreta correctamente.
# PyMuPDF proporciona una extracción de texto Unicode más robusta para estos
# documentos. empleo_admin.py sigue usando la interfaz PdfReader, pero aquí
# se sustituye su implementación antes de que el módulo sea importado.
import pypdf
import fitz


class _PyMuPDFPage:
    def __init__(self, page):
        self._page = page

    def extract_text(self):
        return self._page.get_text("text") or ""


class _PyMuPDFReader:
    def __init__(self, stream):
        data = stream.read() if hasattr(stream, "read") else stream
        self._document = fitz.open(stream=data, filetype="pdf")
        self.pages = [_PyMuPDFPage(page) for page in self._document]


pypdf.PdfReader = _PyMuPDFReader

# Sustituimos la función genérica de extracción por una que identifica la
# convocatoria concreta dentro de publicaciones que contienen varios temarios.
from . import empleo_admin as _empleo_admin
from .temario_extractor import extraer_temario_oficial as _extraer_temario_oficial
from .bop_valencia_rules import aplicar_reglas_bop as _aplicar_reglas_bop

_empleo_admin.extraer_temario_oficial = _extraer_temario_oficial
_aplicar_reglas_bop()
