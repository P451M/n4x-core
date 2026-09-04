workspace "n4x" "Graph-native local runtime with independently revisioned Applications and Experiences." {
    !identifiers hierarchical

    model {
        operator = person "Operator" "Starts the one-process runtime, imports official System zips, and dumps or restores the instance."
        browserUser = person "Browser user" "Uses browser Experience Surfaces on one N4X instance."
        externalMcpHost = softwareSystem "MCP host" "Discovers authoring tools and opens MCP App Surfaces."
        packageOperator = person "Package operator" "Exports and installs Package v2 archives between runtimes."
        neo4j = softwareSystem "Neo4j" "Authoritative v5 graph store with DataSpace-scoped Application data."
        secretBackend = softwareSystem "Secret backend" "macOS Keychain, encrypted local file, or in-memory test backend; values stay outside the graph."

        n4x = softwareSystem "N4X Runtime" "Authors, activates, previews, and serves graph-native Applications and Experiences. One operator per process." {
            host = container "Host" "Install lock, official zip import, materialize, one System worker, reverse proxy, localhost /n4x-host, dump/restore. Never imports System Python." "Python"
            system = container "System" "Worker composition root for authoring, activation, access control, checkpoints, jobs, Experience HTTP, and MCP." "Python"
            developmentRuntime = container "Development Deployment Service" "Pins candidate hashes and binds isolated DataSpaces inside the single runtime stack." "Python"
            actionSupervisor = container "Action Supervisor" "Bounds Action admission, subprocess concurrency, lifecycle, logs, cancellation, and Job heartbeats." "Python"
            surfaceRegistry = container "Surface Type Registry" "Validates versioned browser@1 and mcp_app@1 Surface contracts. PWA is browser.pwa on /." "Python"
            surfaceRuntime = container "Experience Surface Runtime" "Builds mount-agnostic browser and self-contained MCP App artifacts before activation or preview." "Python and Vite"
            httpHost = container "HTTP Surface Host" "System worker Starlette app: browser Surfaces, bridge, invocation, files, callbacks, package staging, and development preview." "Starlette"
            mcpHostAdapter = container "MCP Surface Host" "Publishes authoring tools plus active and development-qualified MCP App resources. Host-control tools call localhost Host." "FastMCP"
            graphAdapter = container "Graph Adapter" "Persists v5 records and composite DataSpace identities through GraphStore and GraphUnitOfWork." "Python and Neo4j"
            packageService = container "Package Service" "Imports and exports n4x.package.v2 archives, including definition-only v4 import into v5 and delete_working_set." "Python"
            sourceStore = container "Source Store" "Stores canonical SourceTrees and SourceFiles in the graph." "Python"
            secretService = container "Secret Service" "Owns secret metadata in the graph and delegates values to an external backend." "Python"
            actionRuntime = container "Action Runtime" "Runs trusted Application subprocesses with DataSpace-scoped snapshots, files, mutations, and Cypher context." "Python"
        }

        operator -> n4x.host "runs n4x serve and host commands on"
        browserUser -> n4x.host "loads Experience routes through"
        externalMcpHost -> n4x.host "calls /mcp or stdio through"
        packageOperator -> n4x.packageService "exports and imports releases through"
        packageOperator -> n4x.httpHost "stages archive bytes via PUT /packages on"
        n4x.host -> n4x.system "supervises the single System worker of"
        n4x.host -> n4x.httpHost "reverse-proxies public HTTP to"
        n4x.host -> n4x.graphAdapter "bootstraps schema and reads enabled SystemRevisions through"
        n4x.host -> n4x.sourceStore "writes official System SourceFiles into"
        n4x.host -> neo4j "dumps and restores the database of"
        n4x.httpHost -> n4x.mcpHostAdapter "mounts MCP HTTP at /mcp on"
        n4x.httpHost -> n4x.system "resolves active and development routes through"
        n4x.mcpHostAdapter -> n4x.system "delegates authoring, development, and Surface-bound calls to"
        n4x.mcpHostAdapter -> n4x.host "delegates official import, enable, dump, and release checks to"
        n4x.system -> n4x.developmentRuntime "creates, resolves, expires, and cleans logical previews in"
        n4x.system -> n4x.actionSupervisor "admits and supervises Action work in"
        n4x.system -> n4x.surfaceRegistry "validates Surface contracts with"
        n4x.system -> n4x.surfaceRuntime "builds all Surfaces before activation or preview in"
        n4x.system -> n4x.sourceStore "authors graph-owned source in"
        n4x.system -> n4x.secretService "records secret and credential metadata in"
        n4x.system -> n4x.graphAdapter "initializes and accesses v5 graph state through"
        n4x.developmentRuntime -> n4x.actionSupervisor "runs candidate Actions with explicit ExecutionContext through"
        n4x.developmentRuntime -> n4x.graphAdapter "creates, clones, binds, and purges isolated DataSpaces through"
        n4x.developmentRuntime -> n4x.surfaceRuntime "resolves candidate-matching Surface artifacts from"
        n4x.actionSupervisor -> n4x.actionRuntime "runs bounded child processes and captures lifecycle in"
        n4x.actionSupervisor -> n4x.graphAdapter "persists Invocation states, logs, and Job heartbeats through"
        n4x.surfaceRuntime -> n4x.surfaceRegistry "dispatches builders by type and version from"
        n4x.surfaceRuntime -> n4x.sourceStore "reads Experience Surface source from"
        n4x.actionRuntime -> n4x.sourceStore "loads Application action source from"
        n4x.actionRuntime -> n4x.secretService "resolves production secret values from"
        n4x.actionRuntime -> n4x.graphAdapter "commits scoped mutations and audits declared Cypher through"
        n4x.sourceStore -> n4x.graphAdapter "persists SourceTrees and SourceFiles through"
        n4x.secretService -> n4x.graphAdapter "persists SecretReferences and CredentialRecords through"
        n4x.secretService -> secretBackend "stores secret values outside the graph in"
        n4x.packageService -> n4x.system "materializes imported Applications and Experiences into"
        n4x.graphAdapter -> neo4j "persists nodes, relationships, artifacts, and DataSpaces in"
    }

    views {
        theme default
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
