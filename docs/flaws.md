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
