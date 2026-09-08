"""Inicialización de la aplicación."""

# El índice de oposiciones del Ayuntamiento de Alicante publica algunos href
# como /oposicion.php?... aunque el detalle real está bajo /rrhh/oposiciones/.
# Normalizamos esas URL antes de que el importador las solicite.
from urllib.parse import urlparse, urlunparse

from . import ayuntamiento_alicante as _ayuntamiento_alicante

_original_enlaces_indice = _ayuntamiento_alicante._enlaces_indice


def _enlaces_indice_corregidos(html: str):
    enlaces = _original_enlaces_indice(html)
    resultado = []
    for titulo, url in enlaces:
        parsed = urlparse(url)
        if (
            parsed.netloc.lower() == "w3.alicante.es"
            and parsed.path.lower().endswith("/oposicion.php")
            and not parsed.path.lower().startswith("/rrhh/oposiciones/")
        ):
            url = urlunparse(
                (
                    parsed.scheme or "https",
                    parsed.netloc,
                    "/rrhh/oposiciones/oposicion.php",
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )
        resultado.append((titulo, url))
    return resultado


_ayuntamiento_alicante._enlaces_indice = _enlaces_indice_corregidos
