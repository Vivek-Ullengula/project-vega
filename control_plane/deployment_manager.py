import boto3
import logging

logger = logging.getLogger(__name__)


class DeploymentManager:
    """Automates deployment of agents to Bedrock AgentCore Runtime."""

    def __init__(self, region_name="us-east-1"):
        self.client = boto3.client("bedrock-agentcore-control", region_name=region_name)

    def deploy_agent_runtime(self, name, container_uri, role_arn, env_vars=None):
        """Creates or updates an AgentCore Runtime deployment idempotently."""
        try:
            logger.info(f"Deploying agent to AgentCore: {name}")

            response = self.client.create_agent_runtime(
                agentRuntimeName=name,
                agentRuntimeArtifact={"containerConfiguration": {"containerUri": container_uri}},
                roleArn=role_arn,
                environmentVariables=env_vars or {},
                networkConfiguration={
                    "networkMode": "PUBLIC"  # Or PRIVATE based on platform config
                },
            )
            runtime_arn = response["agentRuntimeArn"]
            logger.info(f"Agent deployed successfully. ARN: {runtime_arn}")
            return runtime_arn
        except Exception as e:
            if "already exists" in str(e).lower() or "conflictexception" in str(e).lower():
                logger.info(
                    f"Agent runtime '{name}' already exists. Attempting idempotent update mapping..."
                )

                runtime_id = None
                runtime_arn = None
                try:
                    res = self.client.list_agent_runtimes()
                    for r in res.get("agentRuntimes", []):
                        if r.get("agentRuntimeName") == name:
                            runtime_id = r.get("agentRuntimeId")
                            runtime_arn = r.get("agentRuntimeArn")
                            break
                except Exception as list_err:
                    logger.warning(f"Failed to scan agent runtime list: {list_err}")

                if runtime_id:
                    logger.info(f"Updating active cloud runtime target for ID: {runtime_id}")
                    self.client.update_agent_runtime(
                        agentRuntimeId=runtime_id,
                        agentRuntimeArtifact={
                            "containerConfiguration": {"containerUri": container_uri}
                        },
                        roleArn=role_arn,
                        environmentVariables=env_vars or {},
                        networkConfiguration={"networkMode": "PUBLIC"},
                    )
                    logger.info(f"Agent runtime updated successfully. ARN: {runtime_arn}")
                    return runtime_arn or name

            logger.error(f"Failed to deploy agent: {e}")
            raise

    def delete_deployment(self, runtime_arn):
        """Deletes an AgentCore Runtime deployment."""
        try:
            logger.info(f"Deleting deployment: {runtime_arn}")
            self.client.delete_agent_runtime(agentRuntimeArn=runtime_arn)
            logger.info("Deployment deleted.")
        except Exception as e:
            logger.error(f"Failed to delete deployment: {e}")
            raise
