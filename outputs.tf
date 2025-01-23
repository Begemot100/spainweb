output "eks_cluster_id" {
  value = module.eks.cluster_id
}

output "eks_endpoint" {
  value = module.eks.cluster_endpoint
}

output "eks_certificate_authority" {
  value = module.eks.cluster_certificate_authority_data
}
