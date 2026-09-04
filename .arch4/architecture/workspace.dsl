workspace "n4x" "Graph-native local runtime with independently revisioned Applications and Experiences." {
    !identifiers hierarchical

    model {
        browserUser = person "Browser user" "Uses browser Experience Surfaces on one N4X instance."
        externalMcpHost = softwareSystem "MCP host" "Discovers authoring tools and opens MCP App Surfaces."
        packageOperator = person "Package operator" "Exports and installs Package v2 archives between runtimes."
        neo4j = softwareSystem "Neo4j" "Authoritative v5 graph store with DataSpace-scoped Application data."
        secretBackend = softwareSystem "Secret backend" "macOS Keychain or encrypted local file; values stay outside the graph."

        n4x = softwareSystem "N4X Runtime" "Authors, activates, previews, and serves graph-native Applications and Experiences. One operator per process." {
            host = container "Host" "Reverse proxy, file lock, zip import, materialize, localhost enable, dump/restore. Never imports System." "Python"
            system = container "System" "Worker composition root for authoring, activation, access control, checkpoints, jobs, Experience HTTP, and MCP." "Python"
            developmentRuntime = container "Development Deployment Service" "Pins candidate hashes and binds isolated DataSpaces inside the single runtime stack." "Python"
            actionSupervisor = container "Action Supervisor" "Bounds Action admission, subprocess concurrency, lifecycle, logs, cancellation, and Job heartbeats." "Python"
            surfaceRegistry = container "Surface Type Registry" "Validates versioned browser@1 and mcp_app@1 Surface contracts." "Python"
            surfaceRuntime = container "Experience Surface Runtime" "Builds mount-agnostic browser and self-contained MCP App artifacts." "Python and Vite"
            httpHost = container "HTTP Surface Host" "Serves production and deployment-qualified browser, bridge, invocation, file, callback, and MCP HTTP routes." "Starlette"
            mcpHostAdapter = container "MCP Surface Host" "Publishes authoring tools plus active and development-qualified MCP App resources." "FastMCP"
            graphAdapter = container "Graph Adapter" "Persists v5 records and composite DataSpace identities through GraphStore and GraphUnitOfWork." "Python and Neo4j"
            packageService = container "Package Service" "Imports and exports n4x.package.v2 archives, including definition-only v4 import into v5." "Python"
            sourceStore = container "Source Store" "Stores canonical SourceTrees and SourceFiles in the graph." "Python"
            secretService = container "Secret Service" "Owns secret metadata in the graph and delegates values to an external backend." "Python"
            actionRuntime = container "Action Runtime" "Runs trusted Application subprocesses with DataSpace-scoped snapshots, files, mutations, and Cypher context." "Python"
        }

        browserUser -> n4x.httpHost "Loads active or development Experience routes" "HTTPS"
        externalMcpHost -> n4x.mcpHostAdapter "Lists tools/resources and calls Surface-bound tools" "MCP"
        packageOperator -> n4x.packageService "Exports and imports releases" "Package v2"
        n4x.host -> n4x.system "Supervises one System worker and reverse-proxies HTTP"
        n4x.httpHost -> n4x.mcpHostAdapter "Mounts MCP HTTP at /mcp in the same process"
        n4x.httpHost -> n4x.system "Resolves active and development routes through SystemRuntime"
        n4x.mcpHostAdapter -> n4x.system "Delegates authoring, development, and Surface-bound calls"
        n4x.system -> n4x.developmentRuntime "Creates, resolves, expires, and cleans logical previews"
        n4x.system -> n4x.actionSupervisor "Admits and supervises Action work"
        n4x.system -> n4x.surfaceRegistry "Validates Surface contracts"
        n4x.system -> n4x.surfaceRuntime "Builds all Surfaces before activation or preview"
        n4x.system -> n4x.sourceStore "Authors graph-owned source"
        n4x.system -> n4x.secretService "Records secret and credential metadata"
        n4x.system -> n4x.graphAdapter "Initializes and accesses v5 graph state"
        n4x.developmentRuntime -> n4x.actionSupervisor "Runs candidate Actions with explicit ExecutionContext"
        n4x.developmentRuntime -> n4x.graphAdapter "Creates, clones, binds, and purges isolated DataSpaces"
        n4x.developmentRuntime -> n4x.surfaceRuntime "Resolves candidate-matching Surface artifacts"
        n4x.actionSupervisor -> n4x.actionRuntime "Runs bounded child processes and captures lifecycle"
        n4x.actionSupervisor -> n4x.graphAdapter "Persists Invocation states, logs, and Job heartbeats"
        n4x.surfaceRuntime -> n4x.surfaceRegistry "Dispatches builders by type and version"
        n4x.surfaceRuntime -> n4x.sourceStore "Reads Experience Surface source"
        n4x.actionRuntime -> n4x.sourceStore "Loads Application action source"
        n4x.actionRuntime -> n4x.secretService "Resolves production secret values"
        n4x.actionRuntime -> n4x.graphAdapter "Commits scoped mutations and audits declared Cypher"
        n4x.sourceStore -> n4x.graphAdapter "Persists SourceTrees and SourceFiles"
        n4x.secretService -> n4x.graphAdapter "Persists SecretReferences and CredentialRecords"
        n4x.secretService -> secretBackend "Stores secret values outside the graph"
        n4x.packageService -> n4x.system "Materializes imported Applications and Experiences"
        n4x.graphAdapter -> neo4j "Persists nodes, relationships, artifacts, and DataSpaces" "Bolt"
    }

    views {
        systemContext n4x "SystemContext" {
            include *
            autolayout lr
        }
        container n4x "Containers" {
            include *
            autolayout lr
        }
    }
}
