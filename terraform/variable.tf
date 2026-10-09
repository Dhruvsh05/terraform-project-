variable "db_password" {
  type      = string
  sensitive = true
}

variable "key_name" {
  type        = string
  description = "Existing EC2 key pair name (SSH ke liye)"
}