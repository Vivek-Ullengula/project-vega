import boto3
import logging

logger = logging.getLogger(__name__)


class KBManager:
    """Automates creation and management of Bedrock Knowledge Bases."""

    def __init__(self, region_name="us-east-1"):
        self.client = boto3.client("bedrock-agent", region_name=region_name)

    def get_kb_id_by_name(self, name: str):
        """Finds an existing Knowledge Base by name."""
        try:
            response = self.client.list_knowledge_bases()
            for kb in response.get("knowledgeBaseSummaries", []):
                if kb.get("name") == name:
                    return kb.get("knowledgeBaseId")
            return None
        except Exception:
            return None

    def get_data_source_id(self, kb_id: str, name: str):
        """Finds an existing Data Source by name."""
        try:
            response = self.client.list_data_sources(knowledgeBaseId=kb_id)
            for ds in response.get("dataSourceSummaries", []):
                if ds.get("name") == name:
                    return ds.get("dataSourceId")
            return None
        except Exception:
            return None

    def create_kb(self, name, description, role_arn, embedding_model_arn, storage_config):
        """Creates a Knowledge Base."""
        try:
            logger.info(f"Creating Knowledge Base: {name}")
            response = self.client.create_knowledge_base(
                name=name,
                description=description,
                roleArn=role_arn,
                knowledgeBaseConfiguration={
                    "type": "VECTOR",
                    "vectorKnowledgeBaseConfiguration": {"embeddingModelArn": embedding_model_arn},
                },
                storageConfiguration=storage_config,
            )
            kb_id = response["knowledgeBase"]["knowledgeBaseId"]
            logger.info(f"KB created with ID: {kb_id}")
            return kb_id
        except Exception as e:
            logger.error(f"Failed to create KB: {e}")
            raise

    def create_data_source(self, kb_id, name, bucket_arn):
        """Adds an S3 Data Source to the KB."""
        try:
            logger.info(f"Creating Data Source for KB: {kb_id}")
            response = self.client.create_data_source(
                knowledgeBaseId=kb_id,
                name=name,
                dataSourceConfiguration={
                    "type": "S3",
                    "s3Configuration": {"bucketArn": bucket_arn},
                },
            )
            ds_id = response["dataSource"]["dataSourceId"]
            logger.info(f"Data Source created with ID: {ds_id}")
            return ds_id
        except Exception as e:
            logger.error(f"Failed to create Data Source: {e}")
            raise

    def start_ingestion(self, kb_id, ds_id):
        """Starts an ingestion job."""
        try:
            logger.info(f"Starting ingestion for KB: {kb_id}, DS: {ds_id}")
            response = self.client.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
            job_id = response["ingestionJob"]["ingestionJobId"]
            logger.info(f"Ingestion job started: {job_id}")
            return job_id
        except Exception as e:
            logger.error(f"Failed to start ingestion: {e}")
            raise
