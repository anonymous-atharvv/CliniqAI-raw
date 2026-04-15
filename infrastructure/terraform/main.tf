# ============================================================
# CliniQAI — AWS Infrastructure (Terraform)
# HIPAA-eligible architecture with BAA coverage
# Region: ap-south-1 (Mumbai) — for Indian hospitals
# ============================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.0"
    }
  }

  # Remote state in S3 (versioned, encrypted)
  backend "s3" {
    bucket         = "cliniqai-terraform-state"
    key            = "infrastructure/terraform.tfstate"
    region         = "ap-south-1"
    encrypt        = true
    kms_key_id     = "arn:aws:kms:ap-south-1:ACCOUNT_ID:key/STATE_KEY_ID"
    dynamodb_table = "cliniqai-terraform-locks"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "CliniQAI"
      Environment = var.environment
      HIPAA       = "true"
      ManagedBy   = "terraform"
    }
  }
}

# ─────────────────────────────────────────────
# Variables
# ─────────────────────────────────────────────

variable "aws_region"       { default = "ap-south-1" }
variable "environment"      { default = "staging" }
variable "hospital_id"      {}
variable "vpc_cidr"         { default = "10.0.0.0/16" }
variable "db_password"      { sensitive = true }
variable "kafka_password"   { sensitive = true }


# ─────────────────────────────────────────────
# VPC — Private by default
# No public subnets for database or application tiers
# ─────────────────────────────────────────────

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "cliniqai-${var.environment}-vpc"
  cidr = var.vpc_cidr

  azs             = ["${var.aws_region}a", "${var.aws_region}b", "${var.aws_region}c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway     = true
  single_nat_gateway     = var.environment != "production"
  enable_vpn_gateway     = false
  enable_dns_hostnames   = true
  enable_dns_support     = true

  # VPC Flow Logs (required for HIPAA audit)
  enable_flow_log                      = true
  create_flow_log_cloudwatch_iam_role  = true
  create_flow_log_cloudwatch_log_group = true
  flow_log_cloudwatch_log_group_retention_in_days = 90
}


# ─────────────────────────────────────────────
# KMS Keys — Customer-Managed for PHI Encryption
# ─────────────────────────────────────────────

resource "aws_kms_key" "phi_encryption" {
  description             = "CMK for PHI encryption at rest — ${var.hospital_id}"
  deletion_window_in_days = 30
  enable_key_rotation     = true   # Annual automatic rotation
  multi_region            = false  # Single region for data residency

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow RDS to use the key"
        Effect = "Allow"
        Principal = { Service = "rds.amazonaws.com" }
        Action = ["kms:GenerateDataKey", "kms:Decrypt", "kms:Encrypt"]
        Resource = "*"
      },
      {
        Sid    = "Allow S3 to use the key"
        Effect = "Allow"
        Principal = { Service = "s3.amazonaws.com" }
        Action = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = "*"
      }
    ]
  })

  tags = { Name = "cliniqai-phi-key-${var.hospital_id}" }
}

resource "aws_kms_alias" "phi_encryption" {
  name          = "alias/cliniqai-phi-${var.hospital_id}"
  target_key_id = aws_kms_key.phi_encryption.key_id
}

resource "aws_kms_key" "audit_log" {
  description             = "CMK for audit log encryption — ${var.hospital_id}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags = { Name = "cliniqai-audit-key-${var.hospital_id}" }
}


# ─────────────────────────────────────────────
# RDS PostgreSQL + TimescaleDB
# Multi-AZ for production, Single-AZ for staging
# ─────────────────────────────────────────────

resource "aws_db_subnet_group" "main" {
  name       = "cliniqai-${var.environment}-db-subnet"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "rds" {
  name_prefix = "cliniqai-rds-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
    description     = "Allow EKS nodes to connect to RDS"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "main" {
  identifier        = "cliniqai-${var.environment}-${var.hospital_id}"
  engine            = "postgres"
  engine_version    = "16.2"
  instance_class    = var.environment == "production" ? "db.r6g.xlarge" : "db.t3.medium"
  allocated_storage = 100
  max_allocated_storage = 1000   # Autoscaling up to 1TB

  db_name  = "cliniqai"
  username = "cliniqai_admin"
  password = var.db_password

  # Network
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false   # NEVER public

  # HIPAA: Encryption at rest
  storage_encrypted = true
  kms_key_id        = aws_kms_key.phi_encryption.arn

  # HIPAA: Backup and retention
  backup_retention_period = 7         # 7 days PITR
  backup_window           = "03:00-04:00"
  maintenance_window      = "sun:04:00-sun:05:00"
  deletion_protection     = var.environment == "production"

  # HA for production
  multi_az = var.environment == "production"

  # Performance
  performance_insights_enabled = true
  performance_insights_retention_period = 7

  # Monitoring
  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn

  # Logging (HIPAA audit)
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  # Parameter group for TimescaleDB
  parameter_group_name = aws_db_parameter_group.timescale.name

  tags = { Name = "cliniqai-postgres-${var.hospital_id}" }
}

resource "aws_db_parameter_group" "timescale" {
  family = "postgres16"
  name   = "cliniqai-timescale-${var.environment}"

  parameter {
    name  = "shared_preload_libraries"
    value = "timescaledb,pg_stat_statements"
  }

  parameter {
    name  = "timescaledb.max_background_workers"
    value = "16"
  }

  parameter {
    name  = "max_connections"
    value = "200"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"   # Log queries > 1 second
  }
}


# ─────────────────────────────────────────────
# ElastiCache Redis (Agent State)
# ─────────────────────────────────────────────

resource "aws_elasticache_subnet_group" "main" {
  name       = "cliniqai-${var.environment}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis" {
  name_prefix = "cliniqai-redis-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "cliniqai-${var.environment}-redis"
  description          = "CliniQAI Agent State Cache"
  node_type            = "cache.r6g.large"
  num_cache_clusters   = var.environment == "production" ? 3 : 1

  automatic_failover_enabled = var.environment == "production"
  multi_az_enabled           = var.environment == "production"

  at_rest_encryption_enabled  = true
  transit_encryption_enabled  = true
  auth_token                  = var.kafka_password   # Reuse for Redis auth

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  apply_immediately = var.environment != "production"
}


# ─────────────────────────────────────────────
# S3 Buckets — HIPAA-compliant
# ─────────────────────────────────────────────

# Warm path: historical Parquet records
resource "aws_s3_bucket" "warm_path" {
  bucket = "cliniqai-warm-${var.hospital_id}-${var.environment}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "warm" {
  bucket = aws_s3_bucket.warm_path.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.phi_encryption.arn
    }
  }
}

resource "aws_s3_bucket_versioning" "warm" {
  bucket = aws_s3_bucket.warm_path.id
  versioning_configuration { status = "Enabled" }
}

# WORM Audit Log Bucket (write-once, cannot delete)
resource "aws_s3_bucket" "audit_log" {
  bucket = "cliniqai-audit-worm-${var.hospital_id}-${var.environment}"
}

resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.audit_log.id
  rule {
    default_retention {
      mode  = "COMPLIANCE"   # WORM — cannot override even by root
      years = 6              # HIPAA 6-year minimum
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  bucket = aws_s3_bucket.audit_log.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.audit_log.arn
    }
  }
}

# Cold path: Glacier archive
resource "aws_s3_bucket" "archive" {
  bucket = "cliniqai-archive-${var.hospital_id}-${var.environment}"
}

resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    id     = "glacier-transition"
    status = "Enabled"
    transition {
      days          = 30
      storage_class = "GLACIER_IR"
    }
    expiration {
      days = 2555   # 7 years
    }
  }
}


# ─────────────────────────────────────────────
# EKS Cluster
# ─────────────────────────────────────────────

resource "aws_security_group" "eks_nodes" {
  name_prefix = "cliniqai-eks-nodes-"
  vpc_id      = module.vpc.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = "cliniqai-${var.environment}"
  cluster_version = "1.29"

  vpc_id                         = module.vpc.vpc_id
  subnet_ids                     = module.vpc.private_subnets
  cluster_endpoint_private_access = true
  cluster_endpoint_public_access  = var.environment != "production"

  # Encryption at rest for secrets
  cluster_encryption_config = {
    provider_key_arn = aws_kms_key.phi_encryption.arn
    resources        = ["secrets"]
  }

  eks_managed_node_groups = {
    backend = {
      min_size       = var.environment == "production" ? 3 : 1
      max_size       = 10
      desired_size   = var.environment == "production" ? 3 : 2
      instance_types = ["m6i.xlarge"]
      capacity_type  = "ON_DEMAND"
    }

    ai_inference = {
      min_size       = 1
      max_size       = 5
      desired_size   = 1
      instance_types = ["g4dn.xlarge"]   # GPU for ML inference
      capacity_type  = "ON_DEMAND"
    }
  }
}


# ─────────────────────────────────────────────
# IAM Roles
# ─────────────────────────────────────────────

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "rds_monitoring" {
  name = "cliniqai-rds-monitoring-${var.environment}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}


# ─────────────────────────────────────────────
# Outputs
# ─────────────────────────────────────────────

output "rds_endpoint"         { value = aws_db_instance.main.endpoint }
output "redis_endpoint"       { value = aws_elasticache_replication_group.main.primary_endpoint_address }
output "eks_cluster_name"     { value = module.eks.cluster_name }
output "phi_kms_key_arn"      { value = aws_kms_key.phi_encryption.arn }
output "audit_log_bucket"     { value = aws_s3_bucket.audit_log.id }
output "warm_path_bucket"     { value = aws_s3_bucket.warm_path.id }
output "vpc_id"               { value = module.vpc.vpc_id }
