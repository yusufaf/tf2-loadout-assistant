#!/usr/bin/env node
// Load .env before importing the stack: mirrors quizaroni-cdk.ts's ordering note --
// an env var read at module-eval time inside the stack would otherwise miss a plain
// dotenv.config() call placed after the import.
import "dotenv/config";
import "source-map-support/register";
import { App } from "aws-cdk-lib";
import { Tf2LoadoutStack } from "../service/tf2-loadout-stack";

const app = new App();

const env = {
    account: process.env.account,
    region: process.env.region,
};

const appName = process.env.appName;
const deploymentType = process.env.deploymentType;

new Tf2LoadoutStack(app, `${appName}-${deploymentType}-stack`, {
    env,
    appName,
    deploymentType,
});
