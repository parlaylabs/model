Data Flow
=========

Data enters the system in the form of Entity object. These represent the raw
YAML documents that form a config dir. and an optional schema derived from the
YAML documents 'Kind' property. These documents are indexed by the store and
then passed to the graph object. This plans out the render data by combining
various layers of data (from Components, Environment, Graph and Runtime) to
produce the runtime model. This is composed of model objects which are produced
in the process of planning and validating the graph. The end result is a graph
with Service and Relation objects. This is passed in turn to the Runtime
implmentation where then transforms the model objects into a deployment. 


Entity -> Model -> Graph + Runtime + Environment ==> Deployment data


View from Kubernetes
====================

Using the default deployment plugins any Deployment in Kubernetes will have ConfigMap(s) generated and included as files under ```/etc/model```. These files will represent the configuration passed into Service instances from both the Graph and Environment objects as well as the associated information for their Endpoint configuration. If relations exist in the model the remote endpoint data will be available in that directory as well such that the pods can connect to the remote service. If the Istio plugin is used (and by default it is) ingress and egress should be configured such that those pods can speak to their related services.

It then becomes the job of an entrypoint in the container to consume this information in a way that makes sense. If config changes are needed they can be committed to the model in source control and applied (or pulled by a controller watching the repo TBD) and new ConfigMap(s) will be generated and the Deployment can be rolled. 
