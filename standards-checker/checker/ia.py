"""Fase 2 (IA). Interfaz intercambiable: Modelo.evaluar(criterio, proyecto) -> Resultado.
Por ahora solo existe SinIA: los criterios 'tipo: ia' quedan como revision manual (ver README, pendientes)."""
from .modelo import REVISION, Resultado


class SinIA:
    nombre = "sin-ia"

    def evaluar(self, criterio, proyecto):
        return Resultado(REVISION, None, "Fase 2 (IA) no configurada: revisar manualmente")


def cargar_modelo(nombre=None):
    # Punto de extension: aqui se enchufaran 'ollama' / API gratuita detras de la misma interfaz.
    return SinIA()
