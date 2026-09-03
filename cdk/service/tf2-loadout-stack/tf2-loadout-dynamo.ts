import { AttributeType, BillingMode, Table } from "aws-cdk-lib/aws-dynamodb";
import { RemovalPolicy } from "aws-cdk-lib";
import { Construct } from "constructs";
import { ExtendedStackProps } from "models/stack";

// Single table, three entities (Loadout / Profile / Inventory cache -- see
// CLAUDE.md), one access pattern each via PK/SK, so no GSI is actually queried
// today. PK2/SK2 and PK3/SK3 are still created to match the quizaroni/team-builder
// convention exactly -- an empty GSI costs nothing, and matching the shape is
// worth more than trimming it for a table this small.
export class Tf2LoadoutDynamoDB extends Construct {
    table: Table;

    constructor(scope: Construct, id: string, props: ExtendedStackProps) {
        super(scope, id);
        const { appName = "tf2-loadout", deploymentType = "development" } = props;

        const tableName = `${appName}-${deploymentType}-main`;
        this.table = new Table(this, tableName, {
            tableName,
            partitionKey: { name: "PK", type: AttributeType.STRING },
            sortKey: { name: "SK", type: AttributeType.STRING },
            billingMode: BillingMode.PAY_PER_REQUEST,
            pointInTimeRecovery: true,
            // Expires the Inventory-cache entity's items; every other entity leaves
            // `ttl` unset and is kept forever.
            timeToLiveAttribute: "ttl",
            removalPolicy: RemovalPolicy.RETAIN,
        });

        for (const index of [2, 3]) {
            this.table.addGlobalSecondaryIndex({
                indexName: `PK${index}`,
                partitionKey: { name: `PK${index}`, type: AttributeType.STRING },
                sortKey: { name: `SK${index}`, type: AttributeType.STRING },
            });
        }
    }
}
