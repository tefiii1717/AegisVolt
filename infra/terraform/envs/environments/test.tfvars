# terraform workspace select test && terraform plan -var-file=environments/test.tfvars
# Lo que cambia por ambiente vive en locals.settings (indexado por terraform.workspace);
# aquí solo van ajustes operativos. Todos los ambientes corren en Floci (sin cuenta de AWS).
use_floci                  = true
enable_eks                 = true # EKS emulado por Floci (nodo k3s real)
enable_interface_endpoints = true
nat_mode                   = "none"
web_origins                = ["http://localhost:5174"]
