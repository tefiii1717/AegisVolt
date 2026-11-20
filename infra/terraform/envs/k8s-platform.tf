# Complementos de plataforma en EKS (Helm). Las cargas de AegisVolt se despliegan desde CI
# con Kustomize + Argo Rollouts (deploy/k8s), no desde Terraform.

locals {
  platform = var.enable_eks && !var.use_floci
}

resource "helm_release" "aws_lb_controller" {
  count      = local.platform ? 1 : 0
  name       = "aws-load-balancer-controller"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = "1.10.1"
  namespace  = "kube-system"
  values = [yamlencode({
    clusterName = module.eks[0].cluster_name
    region      = var.region
    vpcId       = module.network.vpc_id
    serviceAccount = {
      name        = "aws-load-balancer-controller"
      annotations = var.create_iam ? { "eks.amazonaws.com/role-arn" = module.lbc_irsa[0].iam_role_arn } : {}
    }
  })]
}

resource "helm_release" "ingress_nginx" {
  count            = local.platform ? 1 : 0
  name             = "ingress-nginx"
  repository       = "https://kubernetes.github.io/ingress-nginx"
  chart            = "ingress-nginx"
  version          = "4.11.3"
  namespace        = "ingress-nginx"
  create_namespace = true
  values = [yamlencode({
    controller = {
      replicaCount = local.env == "prod" ? 3 : 2
      service      = { type = "ClusterIP" } # el NLB lo crea Terraform; se vincula con TargetGroupBinding
      config       = { "use-forwarded-headers" = "true", "enable-opentelemetry" = "false" }
      metrics      = { enabled = true }
    }
  })]
}

# El TargetGroupBinding (target group del NLB → pods de ingress-nginx) lo aplica el pipeline de CD
# con el ARN de `terraform output` (deploy/k8s/platform/target-group-binding.yaml), porque su CRD
# solo existe después de instalar el AWS Load Balancer Controller.

resource "helm_release" "argo_rollouts" {
  count            = local.platform ? 1 : 0
  name             = "argo-rollouts"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-rollouts"
  version          = "2.37.7"
  namespace        = "argo-rollouts"
  create_namespace = true
  values = [yamlencode({
    controller = {
      serviceAccount = {
        name        = "argo-rollouts"
        annotations = var.create_iam ? { "eks.amazonaws.com/role-arn" = aws_iam_role.rollouts[0].arn } : {}
      }
    }
  })]
}

resource "helm_release" "strimzi" {
  count            = local.platform ? 1 : 0
  name             = "strimzi"
  repository       = "https://strimzi.io/charts/"
  chart            = "strimzi-kafka-operator"
  version          = "0.44.0"
  namespace        = "kafka"
  create_namespace = true
  values           = [yamlencode({ watchNamespaces = ["kafka"] })]
}
