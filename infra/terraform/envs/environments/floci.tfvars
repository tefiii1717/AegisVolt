# Emulación local (Floci): sin EKS (usar kind/k3d), sin Interface VPCE de pago.
use_floci                  = true
enable_eks                 = false
enable_interface_endpoints = false
web_origins                = ["http://localhost:5173"]
