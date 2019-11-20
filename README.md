Model
====

Model is a high level model specification language. It is designed to make expressing high level service graph ideas simple while still providing control at the lower levels. This is done using a series of templates and a phase of post template __Kustomize__ invocations as needed. The model (which is internally a graph) can then be transformed for use on a runtime. For example, we can translate this model to run directly on Kubernetes. As best-practice evolves we can change then mappings between the model and the runtime to update an entire fleet at scale. 

While Model uses a YAML formatted document like much of the Kubernetes world it is designed to help minimize how much YAML a product author might have to write to include their container in the connected graph of runtime components.

*It's only a model*

Quickstart
----------

```
$ git clone https://github.com/parlaylabs/model.git
$ cd model
$ pipenv install
$ pipenv shell

# To view the model
>$ model graph -c examples/basic/ develop
>$ xdg-open http://localhost:8080

# To render the model 
>$ model appy -c examples/basic apply -o base
```

Model

*these are WIP and don't reflect the current code, for example there isn't versioning and Environment isn't implement yet*

```
kind: Graph/v1
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

**Graph** - A set of interconnected Applications. Nodes in the graph are Applications and edges are Relations. A graph implements the same interface as Application externally using an optional system to promote Endpoints of included Services which can be exposed or referenced in another graph. This will allow hiding of implementation details while still allowing reuse. 

**Interface** - A high level defintion of a named protocol to expect. This is defined alongside a Endpoint and used to inform a Relation.

**Model** - A graph defining Applications and Relations between them.

**Relation** - A set of one or more endpoints and a selector that indicates activity.

**Runtime** - Any compute platform for which a mapping from model to running code exists.


Workflow
=========

Define components by adding component.yaml specs to git repos. Then register them against a well known endpoint for metadata. Each path into the repo defines a single reusable component. The relationship between component and repo is preserved such that changes in the repo can trigger lifecycle events in the model.


Runtime Operations
-------------------

```model init```

This will verify connectivity with the currently selected runtime and make any updates needed to begin operations.


Component Authoring
-------------------

```model component init gh:ref```

This will index the component.yaml and register it with the public metadata service. If you're using a custom component registry it can be provided here.  Updating a component that is referenced in a graph can in turn trigger a CI/CD workflow using a configured strategy. This means that a commit could force new deploys (for example if the component image version is :latest). However a graph object will typically reference static versions of components and must be updated manually or with the provided tooling.

Graph Authoring
---------------

```model graph init <repo>```

Will create a binding between the gh repo and a named model


Graph Mutation
--------------

Models live in their own repo. Adding components and connecting them can be done by manually editing model.yaml files, however

```model graph plan [--stage]``` 

will produce a plan and validate that the relations between components are satisfied.

If versions of referenced components have changed you may update them by editing the model.yaml or by executing

```model graph upgrade [--stage] [component[:version] ...]```

where omitting component will update all and omitting version will use the latest. 


```model graph apply <stage>``` 

will apply any changes to the graph to the runtime. 

To enforce a rollout of a new version you might upgrade the components and then use the apply command with a **-k** option to add a strategy patch to the rollout manifest. This can apply standard policy around canary, a/b or rolling upgrades. With the support of an operator other strategies can be added in the future.


