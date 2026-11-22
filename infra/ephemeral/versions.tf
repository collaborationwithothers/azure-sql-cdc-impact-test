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

  # Partial configuration: the storage account name comes from -backend-config.
  backend "azurerm" {
    container_name   = "tfstate"
    key              = "ephemeral.tfstate"
    use_azuread_auth = true
  }
}

provider "azurerm" {
  features {}
  storage_use_azuread = true
}
