"""Shared helpers: settings from .env and a verified Neo4j driver."""
import os
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise SystemExit(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return value


def get_driver():
    """Open a driver and fail fast if Neo4j is not reachable."""
    driver = GraphDatabase.driver(
        env("NEO4J_URI", "neo4j://localhost:7687"),
        auth=(env("NEO4J_USER", "neo4j"), env("NEO4J_PASSWORD")),
    )
    driver.verify_connectivity()
    return driver
