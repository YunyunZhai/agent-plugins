## ADDED Requirements

### Requirement: Single search endpoint

The system SHALL expose `POST /api/v1/search` as the sole search entry point.

#### Scenario: Keyword channel search

- **GIVEN** a request with `channel=keyword` and `query="python 安全扫描"`
- **WHEN** the server processes the request
- **THEN** it executes GraphQL keyword recall, optional enrich/readme/rerank steps, and returns structured JSON with `candidates_list`

#### Scenario: Semantic channel search

- **GIVEN** a request with `channel=semantic` and `query="启动快的编码智能体"`
- **WHEN** the server processes the request
- **THEN** it executes sqlite-vec kNN semantic recall, optional enrich/readme/rerank steps, and returns structured JSON with `candidates_list`

#### Scenario: Hybrid channel search

- **GIVEN** a request with `channel=hybrid` and `query="多端同步网盘"`
- **WHEN** the server processes the request
- **THEN** it executes keyword and semantic recall in parallel, merges by union dedup, applies optional enrich/readme/rerank steps, and returns structured JSON with `candidates_list`

#### Scenario: Invalid channel parameter

- **GIVEN** a request with `channel=invalid`
- **WHEN** the server validates the request
- **THEN** it returns HTTP 422 with a validation error listing allowed values

### Requirement: Pipeline step control via parameters

The system SHALL support boolean parameters `enrich`, `readme`, and `rerank` to control which pipeline steps execute.

#### Scenario: Default pipeline (no optional steps)

- **GIVEN** a request with no `enrich`, `readme`, or `rerank` parameters
- **WHEN** the server processes the request
- **THEN** only the recall step (keyword/semantic/hybrid) executes, and results are returned directly

#### Scenario: Full pipeline with all steps

- **GIVEN** a request with `enrich=true`, `readme=true`, `rerank=true`
- **WHEN** the server processes the request
- **THEN** the pipeline executes recall → enrich metrics → fetch README snippets → rerank, and returns the fully processed result

#### Scenario: Selective steps

- **GIVEN** a request with `enrich=true` and `rerank=true` but no `readme`
- **WHEN** the server processes the request
- **THEN** only enrich and rerank steps execute (README fetch is skipped)

### Requirement: Search parameters

The system SHALL accept the following search parameters: `query` (required), `language` (optional), `min_stars` (optional, default 200), `top_k` (optional, default 50), `star_weight` (optional, default 0.03).

#### Scenario: Minimal request

- **GIVEN** a request with only `query="rust http framework"`
- **WHEN** the server processes the request
- **THEN** it uses default values for all optional parameters and returns results

#### Scenario: Custom parameters

- **GIVEN** a request with `query="...", language="python", min_stars=500, top_k=20`
- **WHEN** the server processes the request
- **THEN** results are filtered by Python language, minimum 500 stars, and top 20 candidates

### Requirement: User identification header

The system SHALL accept `X-User-Id` header for billing attribution.

#### Scenario: Request with user ID

- **GIVEN** a request with header `X-User-Id: user-123`
- **WHEN** the server processes the request
- **THEN** the billing record includes `user_id=user-123`

#### Scenario: Request without user ID

- **GIVEN** a request without `X-User-Id` header
- **WHEN** the server processes the request
- **THEN** the billing record uses `user_id=anonymous`

### Requirement: Response format

The system SHALL return JSON responses with consistent structure.

#### Scenario: Successful search response

- **GIVEN** a valid search request
- **WHEN** the search completes successfully
- **THEN** the response contains `query`, `channel`, `candidates` (count), `candidates_list` (array), `pipeline_steps` (list of executed steps), and `elapsed` (timing)

#### Scenario: Error response

- **GIVEN** a request that causes an error (e.g., GitHub API failure)
- **WHEN** the server catches the exception
- **THEN** the response contains `error` (message) and appropriate HTTP status code (5xx for upstream failures, 4xx for client errors)

### Requirement: Health check endpoint

The system SHALL expose `GET /api/v1/health` for monitoring.

#### Scenario: Healthy service

- **GIVEN** the server is running and can connect to the database
- **WHEN** a health check request is received
- **THEN** the response contains `status=ok`, database connection status, and index statistics (repo count, vector count)

### Requirement: CLI backward compatibility

Each script in `scripts/` SHALL remain executable as a standalone CLI tool.

#### Scenario: Direct script execution

- **GIVEN** the reorganized directory structure
- **WHEN** running `python3 -m scripts.search.search_repos --query "test" --json`
- **THEN** the script executes and produces the same output as before reorganization
