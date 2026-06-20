# ADR-001: V2 Frontend Framework

## Decision

V2 will use React, TypeScript, and Vite for the browser client.

## Rejected

Using Vue alongside React, or maintaining two frontend frameworks, is rejected.

## Reason

A single frontend stack lowers maintenance and testing cost. React fits the planned
high-interaction research workstation, Playwright acceptance tests, and the future typed
FastAPI contract. This decision does not authorize frontend implementation in Task 0.

