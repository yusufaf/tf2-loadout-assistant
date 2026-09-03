import { Construct } from "constructs";
import { ExtendedStackProps } from "models/stack";
import { Tf2LoadoutDynamoDB } from "./tf2-loadout-dynamo";

export class Tf2Loadout extends Construct {
    appName: string;
    deploymentType: string;

    constructor(scope: Construct, id: string, props: ExtendedStackProps) {
        super(scope, id);

        const { appName = "tf2-loadout", deploymentType = "development" } = props;
        this.appName = appName;
        this.deploymentType = deploymentType;

        new Tf2LoadoutDynamoDB(scope, `${appName}-${deploymentType}-dynamoDB`, props);
    }
}
