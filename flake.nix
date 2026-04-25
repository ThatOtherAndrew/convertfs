let
  name = "convertfs";
  description = "File format conversion as a FUSE filesystem";
  system = "x86_64-linux";
in {
  inherit description;

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
  };

  outputs = { self, nixpkgs }:
  let
    pkgs = nixpkgs.legacyPackages.${system};
  in {
    devShells.${system}.default = pkgs.mkShell {
      inherit name;
      packages = with pkgs; [
        uv
      ];
    };
  };
}
