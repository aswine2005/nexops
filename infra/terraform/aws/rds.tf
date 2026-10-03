resource "aws_db_subnet_group" "nexops" {
  count      = var.enable_rds ? 1 : 0
  name       = "${var.project}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "db" {
  count       = var.enable_rds ? 1 : 0
  name        = "${var.project}-db"
  description = "PostgreSQL reachable only from the NexOps host"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.host.id]
  }
}

resource "aws_db_instance" "nexops" {
  count                        = var.enable_rds ? 1 : 0
  identifier                   = "${var.project}-db"
  engine                       = "postgres"
  engine_version               = "17"
  instance_class               = var.rds_instance_class
  allocated_storage            = 20
  max_allocated_storage        = 100
  storage_type                 = "gp3"
  storage_encrypted            = true
  db_name                      = "nexops"
  username                     = "nexops"
  password                     = random_password.nexops_db.result
  db_subnet_group_name         = aws_db_subnet_group.nexops[0].name
  vpc_security_group_ids       = [aws_security_group.db[0].id]
  backup_retention_period      = 7
  deletion_protection          = true
  skip_final_snapshot          = false
  final_snapshot_identifier    = "${var.project}-db-final"
  performance_insights_enabled = false
  publicly_accessible          = false
}
