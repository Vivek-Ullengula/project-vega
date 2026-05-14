# adapters/aws/bedrock_kb_manager.py
"""Dynamic Bedrock Knowledge Base lifecycle management via boto3."""

import uuid
import structlog
import boto3
from botocore.exceptions import ClientError

logger = structlog.get_logger(__name__)


class BedrockKBManager:
    """Create, sync, query status, and delete Bedrock Knowledge Bases programmatically."""

    def __init__(
        self,
        region: str = "us-east-1",
        role_arn: str = "",
        embedding_model_arn: str = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0",
    ):
        self.region = region
        self.role_arn = role_arn
        self.embedding_model_arn = embedding_model_arn
        self.client = boto3.client("bedrock-agent", region_name=region)
        self._rds_resource_arn = ""
        self._rds_credentials_secret_arn = ""
        logger.info("bedrock_kb_manager_initialized", region=region)

    def create_knowledge_base(
        self,
        name: str,
        description: str,
        rds_resource_arn: str,
        rds_credentials_secret_arn: str,
        rds_database_name: str = "postgres",
        rds_table_name: str = "bedrock_kb",
    ) -> dict:
        """Create a new Bedrock Knowledge Base backed by Aurora PostgreSQL (pgvector)."""
        try:
            response = self.client.create_knowledge_base(
                clientToken=str(uuid.uuid4()),
                name=name,
                description=description,
                roleArn=self.role_arn,
                knowledgeBaseConfiguration={
                    "type": "VECTOR",
                    "vectorKnowledgeBaseConfiguration": {
                        "embeddingModelArn": self.embedding_model_arn,
                    },
                },
                storageConfiguration={
                    "type": "RDS",
                    "rdsConfiguration": {
                        "resourceArn": rds_resource_arn,
                        "credentialsSecretArn": rds_credentials_secret_arn,
                        "databaseName": rds_database_name,
                        "tableName": rds_table_name,
                        "fieldMapping": {
                            "primaryKeyField": "id",
                            "vectorField": "embedding",
                            "textField": "chunks",
                            "metadataField": "metadata",
                        },
                    },
                },
            )
            kb = response["knowledgeBase"]
            logger.info("kb_created", kb_id=kb["knowledgeBaseId"], name=name, status=kb["status"])
            return kb
        except ClientError as e:
            logger.error("kb_creation_failed", name=name, error=str(e))
            raise

    def add_s3_data_source(
        self,
        kb_id: str,
        s3_bucket: str,
        s3_prefix: str = "",
        data_source_name: str | None = None,
    ) -> dict:
        """Add an S3 folder as a data source to an existing Knowledge Base."""
        ds_name = data_source_name or f"{s3_bucket}-{s3_prefix.strip('/')}"
        try:
            s3_config: dict = {"bucketArn": f"arn:aws:s3:::{s3_bucket}"}
            if s3_prefix:
                s3_config["inclusionPrefixes"] = [s3_prefix]

            response = self.client.create_data_source(
                knowledgeBaseId=kb_id,
                clientToken=str(uuid.uuid4()),
                name=ds_name,
                dataSourceConfiguration={
                    "type": "S3",
                    "s3Configuration": s3_config,
                },
            )
            ds = response["dataSource"]
            logger.info(
                "data_source_added", kb_id=kb_id, ds_id=ds["dataSourceId"], bucket=s3_bucket
            )
            return ds
        except ClientError as e:
            logger.error("data_source_add_failed", kb_id=kb_id, error=str(e))
            raise

    def sync_data_source(self, kb_id: str, data_source_id: str) -> dict:
        """Trigger an ingestion job to sync S3 data into the vector store."""
        try:
            response = self.client.start_ingestion_job(
                knowledgeBaseId=kb_id,
                dataSourceId=data_source_id,
            )
            job = response["ingestionJob"]
            logger.info(
                "ingestion_started", kb_id=kb_id, ds_id=data_source_id, job_id=job["ingestionJobId"]
            )
            return job
        except ClientError as e:
            logger.error("ingestion_start_failed", kb_id=kb_id, error=str(e))
            raise

    def get_kb_status(self, kb_id: str) -> dict:
        """Get the current status and details of a Knowledge Base."""
        try:
            response = self.client.get_knowledge_base(knowledgeBaseId=kb_id)
            return response["knowledgeBase"]
        except ClientError as e:
            logger.error("kb_status_check_failed", kb_id=kb_id, error=str(e))
            raise

    def list_knowledge_bases(self) -> list[dict]:
        """List all Knowledge Bases in the account/region."""
        try:
            response = self.client.list_knowledge_bases(maxResults=100)
            return response.get("knowledgeBaseSummaries", [])
        except ClientError as e:
            logger.error("kb_list_failed", error=str(e))
            raise

    def delete_knowledge_base(self, kb_id: str) -> None:
        """Delete a Knowledge Base (and its associated data sources)."""
        try:
            self.client.delete_knowledge_base(knowledgeBaseId=kb_id)
            logger.info("kb_deleted", kb_id=kb_id)
        except ClientError as e:
            logger.error("kb_deletion_failed", kb_id=kb_id, error=str(e))
            raise
