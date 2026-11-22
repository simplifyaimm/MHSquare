-- Runs once on first postgres start. The app DB is created from POSTGRES_DB;
-- LiteLLM gets its own database for keys and spend logs.
CREATE DATABASE litellm;
