// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "FrigateMac",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "FrigateMac", targets: ["FrigateMac"]),
    ],
    targets: [
        .executableTarget(
            name: "FrigateMac",
            path: "FrigateMac",
            exclude: ["Resources"]
        ),
        .testTarget(
            name: "FrigateMacTests",
            dependencies: ["FrigateMac"],
            path: "FrigateMacTests"
        ),
    ],
    swiftLanguageVersions: [.v5]
)
