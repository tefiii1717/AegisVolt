# terraform workspace select dev && terraform plan -var-file=environments/dev.tfvars
# Lo que cambia por ambiente vive en locals.settings (indexado por terraform.workspace);
# aquí solo van ajustes operativos (costos, accesos, orígenes web).
enable_eks                 = true
enable_interface_endpoints = true
nat_mode                   = "none"
web_origins                = ["http://localhost:5173", "https://dev.aegisvolt.example"]
