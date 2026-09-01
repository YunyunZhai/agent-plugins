## ADDED Requirements

### Requirement: Per-request billing record

The system SHALL record a billing entry for every search API call.

#### Scenario: Successful search billing

- **GIVEN** a search request from `user-123` that returns 20 candidates
- **WHEN** the search completes
- **THEN** a billing record is created with `user_id`, `timestamp`, `channel`, `candidates_count`, `call_count=1`, and `token_usage` (if applicable)

#### Scenario: Failed search billing

- **GIVEN** a search request that fails with a 5xx error
- **WHEN** the error is returned
- **THEN** a billing record is still created with `error=true` and `call_count=1`

### Requirement: Token usage tracking

The system SHALL track token consumption for API calls that incur token costs (embedding, rerank).

#### Scenario: Semantic search token tracking

- **GIVEN** a semantic search request that calls the embedding API
- **WHEN** the embedding call completes
- **THEN** the billing record includes `embedding_tokens` with the token count

#### Scenario: Rerank token tracking

- **GIVEN** a request with `rerank=true` that calls the rerank API
- **WHEN** the rerank call completes
- **THEN** the billing record includes `rerank_tokens` with the token count

### Requirement: Billing storage

The system SHALL persist billing records to a SQLite database.

#### Scenario: Billing database initialization

- **GIVEN** the server starts for the first time
- **WHEN** the billing module initializes
- **THEN** it creates a `billing` table if it does not exist

#### Scenario: Billing record persistence

- **GIVEN** a completed search request
- **WHEN** the billing record is written
- **THEN** it is persisted to the billing SQLite database and survives server restarts

### Requirement: Billing query API

The system SHALL expose `GET /api/v1/billing/summary` for usage summary.

#### Scenario: User usage summary

- **GIVEN** a query parameter `user_id=user-123` and `period=2026-09`
- **WHEN** the billing summary is requested
- **THEN** the response contains total `call_count`, total `token_usage`, and breakdown by `channel`
