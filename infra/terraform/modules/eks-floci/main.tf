# EKS en Floci: el emulador levanta un nodo k3s REAL (contenedor Docker) por cada clúster.
# Se usa el recurso nativo aws_eks_cluster (el módulo comunitario depende de APIs de launch
# templates/OIDC que no hacen falta en el emulador). El kubeconfig se obtiene con
# `aws eks update-kubeconfig --endpoint-url http://localhost:4566 --name aegisvolt-<env>`.

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "aegisvolt-eks-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_eks_cluster" "this" {
  #checkov:skip=CKV_AWS_39:Clúster local del emulador; el endpoint solo existe en la máquina de desarrollo.
  #checkov:skip=CKV_AWS_38:Ídem.
  #checkov:skip=CKV_AWS_58:El cifrado de secretos con KMS no aplica al k3s del emulador.
  #checkov:skip=CKV_AWS_37:Los logs del plano de control no existen en k3s emulado.
  #tfsec:ignore:aws-eks-no-public-cluster-access
  #tfsec:ignore:aws-eks-no-public-cluster-access-to-cidr
  #tfsec:ignore:aws-eks-encrypt-secrets
  #tfsec:ignore:aws-eks-enable-control-plane-logging
  name     = "aegisvolt-${var.environment}"
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = true
  }
}
