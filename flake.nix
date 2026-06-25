{
  description = "Payment Processor development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;

      pkgsFor = system: import nixpkgs {
        inherit system;
        config = {
          allowUnfreePredicate = pkg: nixpkgs.lib.getName pkg == "terraform";
        };
      };
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            name = "paymentprocessor-devshell";

            buildInputs = with pkgs; [
              python312
              kubectl
              kubernetes-helm
              terraform
              awscli2
              eksctl
              direnv
            ];

            shellHook = ''
              if [ ! -d ".venv" ]; then
                python -m venv .venv
                source .venv/bin/activate
                if [ -f "requirements.txt" ]; then
                  pip install -r requirements.txt --quiet
                else
                  pip install fastapi "uvicorn[standard]" pydantic sqlalchemy psycopg2-binary alembic --quiet
                fi
                touch .venv/.initialized
              else
                source .venv/bin/activate
                if [ -f "requirements.txt" ] && [ requirements.txt -nt .venv/.initialized ]; then
                  pip install -r requirements.txt --quiet
                  touch .venv/.initialized
                fi
              fi
            '';
          };
        });
    };
}
