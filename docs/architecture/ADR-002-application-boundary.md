# ADR-002: Application Service Boundary

## Decision

Every external interface reaches V1 orchestration through `tifq.application`.

```text
Streamlit / CLI / future FastAPI
              ↓
       Application Services
              ↓
       Existing Python Core
```

React and FastAPI must not call data, backtest, workflow, or runtime modules directly.
Application DTOs contain serializable values and artifact paths, not Streamlit, Plotly,
HTTP, open-file, or subprocess objects.

The V1 Streamlit adapter temporarily uses an application-owned compatibility surface to
preserve its frozen interaction behavior. New interfaces use the facade services directly.
