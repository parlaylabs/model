Pulp
====

Pulp is a high level model specification language. It is designed to make expressing high level service graph ideas simple while still providing control at the lower levels. This is done using a series of templates and a phase of post template __Kustomize__ invocations as needed. 

While pulp uses a YAML formatted document like much of the Kubernetes world it is designed to help minimize how much YAML a product author might have to write to include their container in the connected graph of runtime components.


Quickstart
----------

Model

```
kind: Model/v1
components:
    frontend:
        component: flubbber:1.2.3
        depends: 
            - datastore:pgsql
        expose: 
            - public:http
            - public:https
    datastore:
        component: postgresql:12
        expose:
                - private:pgsql
```

Component

```
kind: Component/v1
name: flubber
image: flubber:1.2.3
interfaces:
    - pgsql
```

Environment
```
kind: Environment/v1
provider:
    compute: aws
    platform: eks:kubernetes:1.16 
    network: consul-connect:latest
    ingress: 
        host: nlb-aws.com
```

Concepts
--------

**Application** - An address and interface through which business use cases are provided. Typically this includes some exposed IP address and an idea of how to comunicate at that address. 

**Component** - An Application definition of attributes and workload known at build time. These define the a re-useable unit of deployment w/o runtime bindings.

**Endpoint** - An address, an interface and a version

**Graph** - A set of interconnected Applications. Nodes in the graph are Applications and edges are Relations. A graph implements the same interface as Component externally using an optional system to promote endpoints to the Component which can be exposed or referenced in another graph. 

**Interface** - A high level defintion of a named protocol to expect. This is defined alongside a Endpoint and used to inform a Relation.

**Model** - A graph defining Applications and Relations between them.

**Relation** - A set of one or more endpoints and a selector that indicates activity.

**Runtime** - Any compute platform for which a mapping from model to running code exists.


Workflow
=========

Define components by adding component.yaml specs to git repos. Then register them against a well known endpoint for metadata. Each path into the repo defines a single reusable component. The relationship between component and repo is preserved such that changes in the repo can trigger lifecycle events in the model.


Runtime Operations
-------------------

```pulp init```

This will verify connectivity with the currently selected runtime and make any updates needed to begin operations.


Component Authoring
-------------------

```pulp component init gh:ref```

This will index the component.yaml and register it with the public metadata service. If you're using a custom component registry it can be provided here.  Updating a component that is referenced in a graph can in turn trigger a CI/CD workflow using a configured strategy. This means that a commit could force new deploys (for example if the component image version is :latest). However a graph object will typically reference static versions of components and must be updated manually or with the provided tooling.

Graph Authoring
---------------

```pulp graph init <repo>```

Will create a binding between the gh repo and a named model


Graph Mutation
--------------

Models live in their own repo. Adding components and connecting them can be done by manually editing model.yaml files, however

```pulp graph plan [--stage]``` 

will produce a plan and validate that the relations between components are satisfied.

If versions of referenced components have changed you may update them by editing the model.yaml or by executing

```pulp graph upgrade [--stage] [component[:version] ...]```

where omitting component will update all and omitting version will use the latest. 


```pulp graph apply <stage>``` 

will apply any changes to the graph to the runtime. 

To enforce a rollout of a new version you might upgrade the components and then use the apply command with a **-k** option to add a strategy patch to the rollout manifest. This can apply standard policy around canary, a/b or rolling upgrades. With the support of an operator other strategies can be added in the future.


