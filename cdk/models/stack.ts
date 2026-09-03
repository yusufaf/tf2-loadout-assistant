import { App, StackProps } from "aws-cdk-lib";
import { Construct } from "constructs";

export interface ExtendedStackProps extends StackProps {
    appName?: string;
    deploymentType?: string;
}

export interface StackConstructsProps {
    scope: App;
    id: string;
    props: ExtendedStackProps;
    construct: Construct;
}
