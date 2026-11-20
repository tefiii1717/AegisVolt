# Clúster EKS administrado por ambiente (módulo oficial de la comunidad).
# Nodos en subredes PRIVADAS sin NAT: imágenes desde ECR y llamadas a AWS por VPC Endpoints.

module "eks" {
  #checkov:skip=CKV_TF_1:Módulo del registro fijado por versión semántica (~> 20.31).
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  cluster_name    = "aegisvolt-${var.environment}"
  cluster_version = var.kubernetes_version

  vpc_id     = var.vpc_id
  subnet_ids = var.private_subnet_ids

  # API del plano de control: privada para los nodos, pública solo desde los CIDR autorizados (runners de CI).
  cluster_endpoint_private_access      = true
  cluster_endpoint_public_access       = length(var.public_access_cidrs) > 0
  cluster_endpoint_public_access_cidrs = var.public_access_cidrs

  enable_irsa                              = var.create_iam
  create_iam_role                          = var.create_iam
  iam_role_arn                             = var.create_iam ? null : var.existing_iam_role_arn
  enable_cluster_creator_admin_permissions = true
  authentication_mode                      = "API_AND_CONFIG_MAP"

  cluster_enabled_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
  cluster_encryption_config = { resources = ["secrets"] }

  cluster_addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true, configuration_values = jsonencode({ enableNetworkPolicy = "true" }) }
    eks-pod-identity-agent = {}
  }

  eks_managed_node_groups = {
    general = {
      instance_types  = var.node_instance_types
      capacity_type   = var.node_capacity_type
      min_size        = var.node_min
      max_size        = var.node_max
      desired_size    = var.node_desired
      create_iam_role = var.create_iam
      iam_role_arn    = var.create_iam ? null : var.existing_iam_role_arn
      labels          = { "aegisvolt.io/environment" = var.environment }
      metadata_options = {
        http_tokens                 = "required" # IMDSv2 obligatorio
        http_put_response_hop_limit = 1          # los pods no pueden robar credenciales del nodo
      }
    }
  }

  # El NLB interno (target type ip) solo puede llegar al puerto de ingress-nginx en los pods.
  node_security_group_additional_rules = {
    ingress_from_nlb = {
      description              = "NLB interno hacia pods de ingress-nginx (target type ip)"
      protocol                 = "tcp"
      from_port                = var.ingress_port
      to_port                  = var.ingress_port
      type                     = "ingress"
      source_security_group_id = var.nlb_security_group_id
    }
  }
}
