# Optional: AWS EKS provisioning (reference; not required for local minikube demo)

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.0" }
  }
}

variable "region" { default = "us-east-1" }
variable "cluster_name" { default = "aiops-eks" }

provider "aws" { region = var.region }

resource "aws_eks_cluster" "aiops" {
  name     = var.cluster_name
  role_arn = aws_iam_role.eks_cluster.arn
  version  = "1.29"

  vpc_config {
    subnet_ids = aws_subnet.private[*].id
  }

  depends_on = [
    aws_eks_cluster_policy.eks_cluster,
  ]
}

resource "aws_iam_role" "eks_cluster" {
  name = "${var.cluster_name}-cluster"
  assume_role_policy = jsonencode({
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
    }]
  })
}

resource "aws_eks_cluster_policy" "eks_cluster" {
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["eks:DescribeCluster"], Resource = "*" }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

# VPC / subnets (minimal)
data "aws_vpc" "default" { default = true }
data "aws_subnet_ids" "private" {
  vpc_id = data.aws_vpc.default.id
  filter = { name = "tag:Name", values = ["*private*"] }
}
resource "aws_subnet" "private" {
  count = 2
  vpc_id = data.aws_vpc.default.id
  cidr_block = "10.0.${count.index}.0/24"
  availability_zone = data.aws_subnet_ids.ids[count.index]
}

# Output kubeconfig (example)
output "cluster_endpoint" {
  value = aws_eks_cluster.aiops.endpoint
}
