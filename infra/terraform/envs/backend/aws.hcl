# Alternativa con AWS real: terraform init -backend-config=backend/aws.hcl
bucket       = "aegisvolt-tfstate-REEMPLAZAR-ACCOUNT-ID"
region       = "us-east-1"
use_lockfile = true
