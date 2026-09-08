"""Inicialización de la aplicación."""

# pypdf puede devolver el carácter de reemplazo (�) en determinados PDF
# oficiales cuya tabla de codificación no se interpreta correctamente.
# PyMuPDF proporciona una extracción de texto Unicode más robusta para estos
# documentos. empleo_admin.py sigue usando la interfaz PdfReader, pero aquí
# se sustituye su implementación antes de que el módulo sea importado.
import io

import fitz
import pypdf


class _PyMuPDFPage:
    def __init__(self, page):
        self._page = page

    def extract_text(self):
        return self._page.get_text("text") or ""


class _PyMuPDFReader:
    def __init__(self, stream):
        if hasattr(stream, "read"):
            data = stream.read()
        else:
            data = stream
        self._document = fitz.open(stream=io.BytesIO(data), filetype="pdf")
        self.pages = [_PyMuPDFPage(page) for page in self._document]


pypdf.PdfReader = _PyMuPDFReader
