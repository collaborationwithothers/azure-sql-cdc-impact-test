terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.14"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Partial configuration. The storage account name is supplied at init time
  # with -backend-config, so no generated name is committed. The very first
  # apply runs against a local backend (see scripts/bootstrap.sh).
  backend "azurerm" {
    container_name   = "tfstate"
    key              = "persistent.tfstate"
    use_azuread_auth = true
  }
}

provider "azurerm" {
  features {}
  storage_use_azuread = true
}
