## ADDED Requirements

### Requirement: Config file loading

The system SHALL load configuration from `config.yaml` in the project root.

#### Scenario: Config file exists

- **GIVEN** a `config.yaml` file at the project root
- **WHEN** the server starts
- **THEN** it reads all configuration values from the file

#### Scenario: Config file missing

- **GIVEN** no `config.yaml` file exists
- **WHEN** the server starts
- **THEN** it uses sensible defaults and logs a warning

### Requirement: Environment variable override

The system SHALL allow environment variables to override config file values.

#### Scenario: Env var overrides config

- **GIVEN** `config.yaml` sets `embedding.backend=local` and env var `GH_SEARCH_BACKEND=dashscope` is set
- **WHEN** the server resolves configuration
- **THEN** the effective backend is `dashscope`

### Requirement: GitHub token configuration

The system SHALL obtain the GitHub token from config or fall back to `gh` CLI credentials.

#### Scenario: Token in config

- **GIVEN** `config.yaml` contains `github.token=ghp_xxx`
- **WHEN** the GitHub client initializes
- **THEN** it uses the configured token

#### Scenario: Token from gh CLI

- **GIVEN** `config.yaml` has no `github.token` and `gh auth status` succeeds
- **WHEN** the GitHub client initializes
- **THEN** it uses the `gh` CLI's stored credentials

### Requirement: Configuration schema

The system SHALL validate the configuration at startup.

#### Scenario: Invalid config

- **GIVEN** `config.yaml` contains an invalid `embedding.backend` value
- **WHEN** the server validates configuration
- **THEN** it logs an error and exits with a clear message indicating the invalid field
