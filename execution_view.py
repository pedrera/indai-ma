"""Session-local association of a displayed result with its own diagnostics."""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from diagnostics import PerformanceSnapshot
from clipboard_text import build_diagnostics_clipboard_text, build_all_clipboard_text, _redact_text


WORKSPACE_EXECUTION_HISTORY_LIMIT = 50


@dataclass(frozen=True)
class WorkspaceExecutionReference:
    """Safe, bounded link from a conversation turn to its captured execution view."""

    operation_id: str
    query_label: str


def append_workspace_execution_reference(history, operation_id: str, query: str):
    if not operation_id:
        raise ValueError("workspace operation ID must be non-empty")
    query_label = _redact_text(" ".join(str(query).split()))[:120] or "Workspace turn"
    retained = [entry for entry in history if entry.operation_id != operation_id]
    retained.append(WorkspaceExecutionReference(operation_id, query_label))
    return tuple(retained[-WORKSPACE_EXECUTION_HISTORY_LIMIT:])

@dataclass(frozen=True)
class ExecutionView:
    operation_id: str
    mode: str
    status: str
    result: Any
    configuration: dict
    snapshot: PerformanceSnapshot
    business_text: str = ''
    tools: list = field(default_factory=list)

    @classmethod
    def capture(cls, mode, result, configuration, snapshot, business_text='', tools=()):
        return cls(snapshot.operation_id, mode, snapshot.status, deepcopy(result),
                   deepcopy(configuration), deepcopy(snapshot), business_text, deepcopy(list(tools)))


def find_execution(executions, operation_id):
    # No fallback to a global/current recorder: absence is explicit.
    return executions.get(operation_id) if operation_id else None


def clipboard_payloads(execution):
    return {
        'response': _redact_text(execution.business_text),
        'diagnostics': build_diagnostics_clipboard_text(execution.snapshot),
        'all': build_all_clipboard_text(execution.business_text, execution.tools, execution.snapshot),
    }


def render_execution_header(execution):
    import streamlit as st
    parts = execution_header_parts(execution)
    st.caption(' · '.join(parts))
    st.caption('Operación: ' + execution.operation_id)


def execution_header_parts(execution):
    """Build header text for both direct Supervisor and public API results."""
    labels = {'completed':'Completado', 'partial':'Parcial', 'needs_input':'Faltan datos',
              'failed':'Error', 'cancelled':'Cancelado', 'timed_out':'Tiempo agotado', 'running':'En curso'}
    parts = [f"Estado: {labels.get(execution.status, execution.status)}",
             f"Duración: {execution.snapshot.elapsed_seconds:.2f} s"]
    config = execution.configuration
    if config.get('strategy'):
        parts.append('Orquestación: ' + config['strategy'])
    result = execution.result
    interpretation = getattr(result, 'interpretation_mode', config.get('interpretation'))
    if interpretation:
        parts.append('Interpretación: ' + {'deterministic':'Determinista', 'llm':'LLM',
            'llm_prioritized':'Priorización LLM', 'deterministic_fallback':'Determinista (fallback)'}.get(interpretation, interpretation))
    if hasattr(result, 'routing'):
        method = getattr(result.routing, 'method', None) or getattr(result.routing, 'routing_method', 'pending')
        parts += ['Routing: ' + method,
                  'Agentes: ' + ', '.join(result.routing.selected_agents)]
        synthesis = getattr(result, 'synthesis_status', None)
        if synthesis:
            parts.append('Síntesis: ' + {'deterministic':'Determinista', 'llm_prioritized':'Priorización LLM',
                                         'deterministic_fallback':'Determinista (fallback)'}.get(synthesis, synthesis))
    return parts
