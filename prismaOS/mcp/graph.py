import os
import logging
from neo4j import AsyncGraphDatabase, exceptions

logger = logging.getLogger("prisma.mcp.graph")

class GraphMemory:
    def __init__(self):
        self.uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = os.getenv("NEO4J_USER", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD")
        self.driver = None

    async def connect(self):
        """Establish and verify the connection to the Neo4j database."""
        if not self.password:
            logger.error("[GRAPH] NEO4J_PASSWORD is not set in the environment.")
            return
            
        try:
            self.driver = AsyncGraphDatabase.driver(self.uri, auth=(self.user, self.password))
            await self.driver.verify_connectivity()
            logger.info("[GRAPH] Successfully connected to Neo4j.")
        except exceptions.ServiceUnavailable as e:
            logger.error(f"[GRAPH] Failed to connect to Neo4j: {e}")
            self.driver = None

    async def close(self):
        """Safely close the database connection."""
        if self.driver:
            await self.driver.close()
            logger.info("[GRAPH] Neo4j connection closed.")

    async def query_graph(self, cypher_query: str, parameters: dict = None) -> list:
        """
        Executes a Cypher query against the Neo4j database.
        Returns a list of dictionaries representing the records.
        """
        if not self.driver:
            logger.error("[GRAPH] Neo4j driver is not initialized. Call connect() first.")
            return []

        parameters = parameters or {}
        
        try:
            async with self.driver.session() as session:
                result = await session.run(cypher_query, parameters)
                records = await result.data()
                return records
        except Exception as e:
            logger.error(f"[GRAPH] Error executing Cypher query: {e}")
            return []

    async def add_entity_relation(self, 
                                  source_label: str, source_id: str, source_props: dict, 
                                  relation_type: str, 
                                  target_label: str, target_id: str, target_props: dict, 
                                  relation_props: dict = None) -> bool:
        """
        Upserts a relationship between two nodes using a Cypher MERGE operation.
        It ensures nodes and relations are created if missing, or updated if they exist.
        """
        if not self.driver:
            logger.error("[GRAPH] Neo4j driver is not initialized.")
            return False

        relation_props = relation_props or {}
        
        # Ensure labels and relation types are safe (they cannot be parameterized natively in Cypher)
        if not (source_label.isalnum() and target_label.isalnum() and relation_type.replace('_', '').isalnum()):
            logger.error("[GRAPH] Invalid labels or relation type provided.")
            return False

        query = f"""
        MERGE (source:{source_label} {{id: $source_id}})
        SET source += $source_props
        MERGE (target:{target_label} {{id: $target_id}})
        SET target += $target_props
        MERGE (source)-[r:{relation_type}]->(target)
        SET r += $relation_props
        RETURN source, r, target
        """

        params = {
            "source_id": source_id,
            "source_props": source_props,
            "target_id": target_id,
            "target_props": target_props,
            "relation_props": relation_props
        }

        try:
            async with self.driver.session() as session:
                await session.run(query, params)
            logger.info(f"[GRAPH] Added relation: ({source_label}:{source_id}) -[{relation_type}]-> ({target_label}:{target_id})")
            return True
        except Exception as e:
            logger.error(f"[GRAPH] Error adding entity relation: {e}")
            return False

# Export a global singleton instance
graph_db = GraphMemory()