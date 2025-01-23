terraform {
  backend "s3" {
    bucket         = "my-terraform-state-bucket100"
    key            = "path/to/terraform.tfstate"
    region         = "eu-central-1"
    dynamodb_table = "terraform-lock-table"
    encrypt        = true
  }
}
