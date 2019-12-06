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
