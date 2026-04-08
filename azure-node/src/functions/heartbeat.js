const { app } = require("@azure/functions");

app.http("heartbeat", {
    methods: ["GET"],
    authLevel: "anonymous",
    handler: async (request, context) => {
        return {
            status: 200,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                status: "healthy",
                provider: "azure",
                region: process.env.REGION_NAME || "israelcentral",
                timestamp: new Date().toISOString(),
            }),
        };
    },
});
