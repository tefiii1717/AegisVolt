"""Infraestructura compartida de AegisVolt.

Regla anti-monolito-distribuido: este paquete solo contiene preocupaciones transversales
(observabilidad, transporte, persistencia técnica). Ningún modelo de dominio vive aquí; cada
bounded context define y valida sus propios payloads.
"""

__version__ = "0.1.0"
