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
$ pipenv install . 
$ pipenv update -e .

# To view the model which will run a server at http://localhost:8080/
$ model graph -c examples/basic/ -c examples/interfaces develop

# To render the model 
$ model graph  -c examples/basic -c examples/interfaces apply -o base
```

Concepts
--------

**Component** - A Service definition of attributes and workload known at
build time. These define the a reusable unit of deployment w/o runtime
bindings. A Service then is the instantiation of a Component.

**Endpoint** - An address, an interface and a version.

**Environment** - A configuration representing where a model is to be run. This
includes non-reusable parts of a deployment, such as things like the public DNS
name associated with a runtime. 

**Graph** - A set of interconnected Services. Nodes in the graph are
Services and edges are Relations. A graph implements the same interface as
Service externally using an optional system to promote Endpoints of
included Services which can be exposed or referenced in another graph. This
will allow hiding of implementation details while still allowing reuse. 

**Interface** - A high level definition of a named protocol to expect. This is
defined alongside a Endpoint and used to inform a Relation.

**Relation** - A set of one or more endpoints and a selector that indicates activity.

**Runtime** - Any compute platform for which a mapping from model to running
code exists. By selecting among various plugins we can determine 'how' the
model will be run. The Runtime object provides translations from the model to a deployment.

**Services** - An address and interface through which business use cases are
provided. Typically this includes some exposed IP address and an idea of how to
communicate at that address. 

Model
=====

**Graph**

A graph defining two services objects connected by a relation. 

```
kind: Graph
name: blog
services:
  - name: ghost
    expose: ["http"]
    replicas: 1
  - name: mysql
relations:
  - ["mysql:db", "ghost:db"]
```

**Component**

Here are the two components, these reference an image used for deployment and interfaces used for relations.

```
kind: Component
name: mysql
image: mysql:5.7
version: 1
endpoints:
  - name: db
    interface: mysql:server
```

```
kind: Component
name: ghost
image: ghost:3-alpine
version: 1
environment:
  requires: ["url"]
endpoints:
  - name: db
    interface: mysql:client
    environment:
      # Required if building a relation with this endpoint
      # this is the component describing what it needs to run
      requires:
        [
          "database__client",
          "database__connection__host",
          "database__connection__port",
          "database__connection__user",
          "database__connection__password",
          "database__connection__database",
        ]
  - name: http
    interface: http:server
    addresses:
      - ports: [2368]
```

This also shows how a component can describe what it needs to run and/or relate. In this example we need the 'url' env set for the component to launch. This image can run with a local database which is included, however to connect it via its db endpoint you'd need to provide the items listed as required (see environment below).


**Interface**

Interface defines what information is exchanged between Services that wish to connect. This is based on the roles they implement. In the above components we can see that ghost implements mysql:client while the mysql component implements the mysql:server portion of the interface.

Values provided through the environment as configuration can be validated using their interface definition to provide some assurance that relationships can be properly created.

```
kind: Interface
name: mysql
version: 5.7
role:
  - name: server
    uses:
      - { name: username, type: str }
      - { name: password, type: str, secret: true }
    provides:
      - { name: address, default: "{service.service_addr}", type: str }
      - { name: port, default: "3306", type: str }
      - { name: admin_user, type: str, default: "root" }
      - { name: admin_password, type: str, secret: true }
  - name: client
    provides: {}
```

**Environment**

If the Graph object provides the generic topology of a deployment the Environment provides specific information about how the deployment will function. This includes specific information that can only be known at deployment time as well as things like credentials specific to a given deployment. 

```
kind: Environment
name: dev
config:
    public_dns: "example.com"
    services:
        ghost:
            environment:
                - name: url
                  value: "http://{service.name}.{public_dns}"
                - name: database__client
                  value: "{db_remote.service.name}"
                - name: database__connection__host
                  value: "{db_remote.service.service_addr}"
                - name: database__connection__port
                  value: "{db_remote.ports[0]}"
                - name: database__connection__user
                  value: "{db_remote.provided.admin_user}"
                - name: database__connection__password
                  value: "{db_remote.provided_secrets.admin_password}"
                - name: database__connection__database
                  value: "{service.name}"
        mysql:
            config:
                - endpoint: "db"
                  data:
                      admin_password: "testing"
            environment:
                - name: MYSQL_ROOT_PASSWORD
                  value: "{db_local.provided_secrets.admin_password}"

```

Config values (see config/services/mysql/config in example above) will be overlaid onto the service and available for [variable interpolation](#variable-interpolation) and [templating](#templates). If the runtime is set to null, we are indicating the deployment of the service isn't managed by model, however we can statically supply config as part of the environment here which will be available in relations to connect to.


Variable Interpolation  <a name="variable-interpolation"></a>
=======================

Resources in the graph undergo a process of variable interpolation which allows them to flexibly address the definitions and configration of connected components within the graph. The Interface and Environment examples above show examples of this. While the data provided need further documentation, it is fair to say common model objects will have `this` and `services` defined and relations will be available as `<endpoint name>`_relation, `<endpoint_name>`_local and `<endpoint_name>`_remote giving access to the attributes defined in model/model.py. This makes it easy to substitute values where needed.


Templates <a name="templates"></a>
=========

Components can define a **files** directive which will run Jinja2 templates with access to the full model context. These files will be mapped into the runtime (in the case of k8s as additional ConfigMaps/Volumes)

  ```
  files:
    - container_path: /etc/model/overrides.conf
      template: overrides.conf
  ```

This allows for the easy inclusion of config files into the container which are still updated and managed via a ConfigMapGenerator. This means that change can easily force new deployment rollout.


Workflow
=========

Define components by adding component.yaml specs to git repos. Then register them against a well known endpoint for metadata. Each path into the repo defines a single reusable component. The relationship between component and repo is preserved such that changes in the repo can trigger life cycle events in the model.


Runtime Operations
-------------------

Graph Mutation
--------------

Models live in their own repo. Adding components and connecting them can be done by manually editing model.yaml files, however

```model graph plan``` 

will produce a plan and validate that the relations between components are satisfied.

If you wish to interactively explore the model you may try 

```model graph shell```

which will give you a REPL with the loaded graph.

[TBD]
If versions of referenced components have changed you may update them by editing the model.yaml or by executing

```model graph upgrade [component[:version] ...]```

where omitting component will update all and omitting version will use the latest. 


```model graph apply -o <dir>``` 

will apply any changes to the graph to the runtime. 

To enforce a rollout of a new version you might upgrade the components and then use the apply command with a **-k** option to add a strategy patch to the rollout manifest. This can apply standard policy around canary, a/b or rolling upgrades. With the support of an operator other strategies can be added in the future.


Runtime
=======

While the notion of runtime as a set of model mapping plugins is flexible, our first target is Kubernetes.

When the model is mapped to k8s a number of things are currently being done behind the scenes. 

* A k8s namespace is created per graph
* k8s Deployments are created 
  * with a standard set of labels and annotations
  * with volumes for /etc/podinfo providing access to k8s metadata
  * with volumes at /etc/model/config with json for all the related services and their interface data
  * with volumes at /etc/model/secrets with json for the related service secrets (more TBD)
* k8s Services are created 
  * with a Istio Virtual Service mapped through the ingressgateway
* Registration of resources in kustomize.yaml
  * to support configmap and secret regeneration
  * customizations not yet directly supported by the model project can be included as overlays
  