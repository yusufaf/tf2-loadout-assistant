import { Stack } from "aws-cdk-lib";
import { Construct } from "constructs";
import { ExtendedStackProps } from "models/stack";
import { Tf2Loadout } from "./tf2-loadout";

export class Tf2LoadoutStack extends Stack {
    constructor(scope: Construct, id: string, props: ExtendedStackProps) {
        super(scope, id, props);

        const { appName, deploymentType } = props;

        new Tf2Loadout(this, `${appName}-${deploymentType}`, props);
    }
}
