schema is registering model types directly
qual_name is a mess, we need graph.namespace.objectname
    ns support will need to be more specific than "graph" (as it is now)

runtime should produce the interpolation context
    properly combining Component->Service/Relation with Runtime and Environment
        relations need to bind the proper settings from their endpoints interfaces.
            templating from one interface to another must pull the proper "produce/consume" side of the data

    The render context/data might then be mapped in as a ConfigMap such that the container can expect
    any data used to configure the objects will be made available. This will
    suppose a whole host of passthrough values which should ease migration. It
    allows going in and backfilling schema definitions which can then be
    checked and enforced from that point forward.


Volume Mgmt/Storage isn't handled well. Components can indicate they need a
volume. The Environment/Runtime should define the mapping. 


Relationship handling
    values are being written from the remote side to the side they are provided to
    these are stored in a dict now
    for multi-client (like many services using a db endpoint on the db) it will need to be indexed differently
        while this is true in the model we still want to support a simple interface (jq friendly) on the consumer side. This either has to be a list structure which is harder to parse without denoting a key or a mapping which again has the key issue. 


Need an idea around PortSets with names that can be overridden at the various config layers. 


GitOps model -- create a binding between a graph, environment and runtime and a daemon running with the deployment. WHen a change occurs in the git upstream (branch and/or tag) we must resolve those change.


Components need a way to define what permissions they might need relative to a runtime. Such that we say "this component on AWS needs the following ec:<whatever> s3:*" permissions to run. This can help us configure calico or kiam to ensure the right pods get the right access and accelerate reviews.