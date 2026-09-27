import { Inngest } from "inngest";
import { connect } from "inngest/connect";

// parallel steps: https://www.inngest.com/docs/guides/step-parallelism#running-steps-in-parallel

console.log("Starting up worker with pid", process.pid);

const app1 = new Inngest({
  id: "my-connect-js-app-1",
  eventKey: "abc123",
  appVersion: "v1.0",
});

const app2 = new Inngest({
  id: "my-connect-js-app-2",
  eventKey: "abc123",
  appVersion: "v1.0",
});

console.log("Connecting...");

connect({
  apps: [
    {
      client: app1,
      functions: [
        app1.createFunction(
          { id: "test-function" },
          { event: "connect-demo/test" },
          async ({ step }) => {
            await step.run("test", async () => {
              console.log("via connect!");
              await new Promise((resolve) => setTimeout(resolve, 10000));
              console.log("step 1 - done");
              return "this works";
            });
            await step.run("test", async () => {
              console.log("via connect!");
              await new Promise((resolve) => setTimeout(resolve, 10000));
              console.log("step 2 - done");
              return "this works";
            });
            await step.run("test", async () => {
              console.log("via connect!");
              await new Promise((resolve) => setTimeout(resolve, 10000));
              console.log("step 3 - done");
              return "this works";
            });
            await step.run("test", async () => {
              console.log("via connect!");
              await new Promise((resolve) => setTimeout(resolve, 100000));
              console.log("step 4 - done");
              return "this works";
            });
            await step.run("test", async () => {
              console.log("via connect!");
              await new Promise((resolve) => setTimeout(resolve, 10000));
              console.log("function done");
              return "this works";
            });

          }
        ),
        app1.createFunction(
          { id: "hello-world" },
          { event: "connect-demo/hello-world" },
          async ({ step }) => {
            return { success: true };
          }
        ),
      ],
    },
    {
      client: app2,
      functions: [
        app2.createFunction(
          { id: "hello-world" },
          { event: "connect-demo/hello-world-2" },
          async ({ step }) => {
            return { success: true };
          }
        ),
      ],
    },
    {
      client: app3,
      functions: [
        app3.createFunction(
          { id: "hello-world" },
          { event: "connect-demo/hello-world-3" },
            async ({ event, step }) => {
                // These steps are not `awaited` and run in parallel when Promise.all
                // is invoked.
                const sendEmail = step.run("confirmation-email", async () => {
                const emailID = await sendEmail(event.data.email);
                return emailID;
                });

                const updateUser = step.run("update-user", async () => {
                return "updated event: " + JSON.stringify(event);
                });

                // Run both steps in parallel.  Once complete, Promise.all will return all
                // parallelized state here.
                //
                // This ensures that all steps complete as fast as possible, and we still have
                // access to each step's data once they're complete.
                const [emailID, updates] = await Promise.all([sendEmail, updateUser]);

                return { emailID, updates };
            }
        ),
      ],
    },
  ],
  instanceId: "my-worker",
  maxWorkerConcurrency: 3,
  rewriteGatewayEndpoint: (endpoint) => {
    return endpoint.replace("connect-gateway:8080", "localhost:8100");
  },
}).then(async (conn) => {
  console.log("Connected!");

  const statusLog = setInterval(() => {
    console.log(conn.state);
  }, 1000);

  await conn.closed;

  console.log("Closed, clearing");
  clearInterval(statusLog);
});
