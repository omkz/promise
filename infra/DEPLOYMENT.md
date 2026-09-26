# Deploying PROMISE to AWS

End-to-end deployment guide for the CloudFormation stacks in `infra/cloudformation/`. Not
run from this repository -- preparation only. Every stack takes `EnvironmentName` (default
`dev`); use the same value across all of them for a given environment, since later stacks
`Fn::ImportValue` earlier stacks' exports by that name.

No account IDs, regions, ARNs, or secrets are hard-coded anywhere -- everything comes from
CloudFormation parameters/pseudo-parameters or `Fn::ImportValue`. Re-running `aws
cloudformation deploy` against the same stack name is safe/idempotent: it only changes
what actually differs.

## 0. Prerequisites

- `aws` CLI configured with credentials for the target account.
- `docker buildx` with the `linux/arm64` platform available (see
  `services/mcp/AGENTCORE.md` for registering QEMU emulation if building on an amd64 host).
- Cognito is **not** required for this MVP -- `AuthMode` defaults to `local` everywhere,
  preserving the seeded dev workspace/user fallback exactly as local development uses it.

## 1. Storage (DynamoDB + S3)

```
aws cloudformation deploy \
  --stack-name promise-dev-storage \
  --template-file infra/cloudformation/storage.yaml \
  --parameter-overrides EnvironmentName=dev DocumentsBucketName=promise-documents-<ACCOUNT_ID>-<REGION> \
  --region <REGION>
```

The `UserOwnedIndex` GSI is created together with the table (`GlobalSecondaryIndexes` in
the `CreateTable` call CloudFormation makes) -- `infra/deploy_gsi.py` is only needed to
migrate a table that already existed *before* this stack, never for a fresh deploy.

## 2. ECR repositories

```
aws cloudformation deploy \
  --stack-name promise-dev-ecr \
  --template-file infra/cloudformation/ecr.yaml \
  --parameter-overrides EnvironmentName=dev \
  --region <REGION>
```

## 3. IAM

```
aws cloudformation deploy \
  --stack-name promise-dev-iam \
  --template-file infra/cloudformation/iam.yaml \
  --parameter-overrides EnvironmentName=dev \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <REGION>
```

## 4. Build and push images (unique tag per build -- ECR repos are IMMUTABLE)

```
TAG=$(git rev-parse --short HEAD)
API_URI=$(aws cloudformation describe-stacks --stack-name promise-dev-ecr --query "Stacks[0].Outputs[?OutputKey=='ApiRepositoryUri'].OutputValue" --output text)
MCP_URI=$(aws cloudformation describe-stacks --stack-name promise-dev-ecr --query "Stacks[0].Outputs[?OutputKey=='McpRepositoryUri'].OutputValue" --output text)
WEB_URI=$(aws cloudformation describe-stacks --stack-name promise-dev-ecr --query "Stacks[0].Outputs[?OutputKey=='WebRepositoryUri'].OutputValue" --output text)
aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin "${API_URI%%/*}"

docker buildx build --platform linux/amd64 -f services/api/Dockerfile -t "$API_URI:$TAG" --push .
docker buildx build --platform linux/arm64 -f services/mcp/Dockerfile -t "$MCP_URI:$TAG" --push .   # AgentCore requires arm64
docker build --build-arg NEXT_PUBLIC_API_URL=https://placeholder.invalid -t "$WEB_URI:$TAG" web/ && docker push "$WEB_URI:$TAG"
```

The web image above is a **placeholder build** -- its real `NEXT_PUBLIC_API_URL` isn't
known until step 5 gives you the API's URL. Rebuild it in step 6.

## 5. services/api (App Runner)

```
aws cloudformation deploy \
  --stack-name promise-dev-api \
  --template-file infra/cloudformation/api-service.yaml \
  --parameter-overrides EnvironmentName=dev "ApiImageIdentifier=$API_URI:$TAG" \
  --region <REGION>
API_URL=$(aws cloudformation describe-stacks --stack-name promise-dev-api --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl'].OutputValue" --output text)
```

## 6. web (App Runner) -- rebuild with the real API URL, then deploy

```
docker build --build-arg NEXT_PUBLIC_API_URL="$API_URL" -t "$WEB_URI:$TAG" web/ && docker push "$WEB_URI:$TAG"
aws cloudformation deploy \
  --stack-name promise-dev-web \
  --template-file infra/cloudformation/web-service.yaml \
  --parameter-overrides EnvironmentName=dev "WebImageIdentifier=$WEB_URI:$TAG" \
  --region <REGION>
WEB_URL=$(aws cloudformation describe-stacks --stack-name promise-dev-web --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl'].OutputValue" --output text)
```

## 7. Tighten CORS now that the web URL is known (in-place update, no rebuild)

```
aws cloudformation deploy \
  --stack-name promise-dev-api \
  --template-file infra/cloudformation/api-service.yaml \
  --parameter-overrides EnvironmentName=dev "ApiImageIdentifier=$API_URI:$TAG" "CorsOrigins=$WEB_URL" \
  --region <REGION>
```

## 8. services/mcp (AgentCore Runtime)

```
aws cloudformation deploy \
  --stack-name promise-dev-mcp \
  --template-file infra/cloudformation/agentcore-runtime.yaml \
  --parameter-overrides EnvironmentName=dev "McpImageIdentifier=$MCP_URI:$TAG" \
  --region <REGION>
```

## What's still manual

- Cognito/OIDC setup, only if/when `AuthMode` is switched from `local` to `oidc` on the
  api/mcp stacks -- not needed for this MVP.
- Attaching `GsiMigrationPolicy` (iam.yaml output) is only relevant when migrating a
  pre-existing table, not this fresh deploy path.
- Whatever invokes the deployed MCP runtime (Alexa+ account-linking, another MCP client)
  needs its own auth setup against the runtime ARN -- out of scope here.
