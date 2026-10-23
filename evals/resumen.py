"""Resume el último resultado de adk eval.

Correr desde la raíz del repo después de adk eval:
    python evals/resumen.py            # casos que fallaron
    python evals/resumen.py --todos    # todos los casos, con la respuesta
    python evals/resumen.py --json salida.json   # datos para otros scripts
"""

import json
import sys
from pathlib import Path

HISTORIAL = Path(__file__).parent.parent / "mindi_agent" / ".adk" / "eval_history"
METRICAS = ("agente_correcto", "mensaje_fijo_correcto", "respuesta_cumple_banco")


def _texto(contenido) -> str:
    if not contenido:
        return ""
    return "".join(p.get("text") or "" for p in contenido.get("parts") or []).strip()


def ultimo_resultado() -> dict:
    archivos = sorted(HISTORIAL.glob("*.evalset_result.json"), key=lambda p: p.stat().st_mtime)
    if not archivos:
        sys.exit(f"No hay resultados en {HISTORIAL}. Corre adk eval primero.")
    datos = json.loads(archivos[-1].read_text(encoding="utf-8"))
    return json.loads(datos) if isinstance(datos, str) else datos


def casos(resultado: dict) -> list[dict]:
    salida = []
    for caso in resultado["eval_case_results"]:
        turnos = []
        for inv in caso["eval_metric_result_per_invocation"]:
            actual = inv["actual_invocation"]
            eventos = (actual.get("intermediate_data") or {}).get("invocation_events") or []
            esperado = {
                r["type"]: r["rubric_content"]["text_property"]
                for r in actual.get("rubrics") or []
                if r.get("type") in ("AGENTE_ESPERADO", "MENSAJE_FIJO")
            }
            fallas = []
            for metrica in inv["eval_metric_results"]:
                if metrica["metric_name"] not in METRICAS:
                    continue
                for rubrica in (metrica.get("details") or {}).get("rubric_scores") or []:
                    if rubrica.get("score") != 1.0:
                        fallas.append((rubrica["rubric_id"], rubrica.get("rationale") or ""))
            turnos.append({
                "mensaje": _texto(actual["user_content"]),
                "respuesta": _texto(actual.get("final_response")),
                "agente": eventos[-1]["author"] if eventos else "desconocido",
                "agente_esperado": esperado.get("AGENTE_ESPERADO"),
                "fallas_rubricas": fallas,
            })
        metricas = {
            m["metric_name"]: m.get("score")
            for m in caso["overall_eval_metric_results"]
            if m["metric_name"] in METRICAS
        }
        salida.append({
            "id": caso["eval_id"],
            "paso": caso["final_eval_status"] == 1,
            "metricas": metricas,
            "turnos": turnos,
        })
    return salida


def main() -> None:
    lista = casos(ultimo_resultado())
    if "--json" in sys.argv:
        destino = Path(sys.argv[sys.argv.index("--json") + 1])
        destino.write_text(json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(lista)} casos -> {destino}")
        return

    todos = "--todos" in sys.argv
    pasan = sum(c["paso"] for c in lista)
    print(f"Pasan {pasan} de {len(lista)}\n")
    for caso in lista:
        if caso["paso"] and not todos:
            continue
        estado = "PASA" if caso["paso"] else "FALLA"
        print(f"== {caso['id']} {estado} {caso['metricas']}")
        for i, turno in enumerate(caso["turnos"], 1):
            print(f"  [{i}] ella: {turno['mensaje']}")
            print(f"      agente: {turno['agente']} (esperado: {turno['agente_esperado']})")
            print(f"      respuesta: {turno['respuesta']}")
            for rubrica, motivo in turno["fallas_rubricas"]:
                print(f"      x {rubrica}: {motivo}")
        print()


if __name__ == "__main__":
    main()
