"""
Based on https://github.com/kijk2869/discodo/blob/master/discodo/client/DPYClient.py
"""
import asyncio
import itertools
from typing import Any, Optional, Union

import dico  # noqa
import discodo  # noqa
from discodo import (
    EventDispatcher,
    NodeNotConnected,
    Nodes,  # noqa
    VoiceClientNotFound)
from discodo.client.node import Node, launchLocalNode  # noqa


class NodeClient(Node):  # type: ignore[call-arg, misc]
    async def onResumed(self, data: dict[str, Any]) -> None:
        await super().onResumed(data)

        for guild_id, vc_data in data["voice_clients"].items():
            guild = self.client.client.get(int(guild_id))
            if "channel" in vc_data and vc_data["channel"]:
                channel = self.client.client.get(int(vc_data["channel"]))
                self.loop.create_task(self.client.connect(channel, self))
            else:
                self.loop.create_task(self.client.disconnect(guild))

    async def close(self) -> None:
        for guildId in self.voiceClients:
            self.loop.create_task(
                self.client.disconnect(self.client.client.get_guild(guildId)))


class DicoClient:
    def __init__(self, client: dico.Client):
        self.client = client
        self.loop = client.loop or asyncio.get_event_loop()

        self.dispatcher = EventDispatcher()

        self.guild_reservation_map: dict[int, discodo.Node] = {}

        self.nodes = Nodes()

        self.client.on_("raw", self.discord_dispatch)

    def __repr__(self) -> str:
        return f"<DicoClient Nodes={self.nodes} voiceClients={len(self.voice_clients)}>"

    @property
    def event(self):  # type: ignore
        return self.dispatcher.event

    async def discord_dispatch(self, payload: dict[str, Any]) -> None:
        if payload["t"] in ["VOICE_STATE_UPDATE", "VOICE_SERVER_UPDATE"]:
            vc = self.get_vc(payload["d"]["guild_id"], safe=True)
            select_nodes = [
                self.guild_reservation_map.get(
                    int(payload["d"]["guild_id"]),
                    (vc.Node if vc else self.get_best_node()),
                )
            ]
        else:
            select_nodes = self.nodes

        nodes_task = [
            node.discordDispatch(payload) for node in select_nodes
            if node and node.is_connected
        ]
        if nodes_task:
            await asyncio.wait(
                nodes_task,
                return_when="ALL_COMPLETED",
            )

    def register_node(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = "hellodiscodo",
        region: Optional[str] = None,
        launch_options: Optional[dict[str, Any]] = None,
    ) -> asyncio.Task:  # type: ignore

        if launch_options is None:
            launch_options = {}

        return self.loop.create_task(
            self.connect_node(host, port, password, region, launch_options))

    async def connect_node(self, host: Optional[str], port: Optional[int],
                           password: Optional[str], region: Optional[str],
                           launch_options: dict[str, Any]) -> None:
        await self.client.wait_ready()

        if not host or not port:
            local_node_process = await launchLocalNode(**launch_options)

            host = local_node_process.HOST
            port = local_node_process.PORT
            password = local_node_process.PASSWORD

        user_id = int(self.client.application_id)
        shard_id = None  # I don't know how to get shard id in dico

        node = NodeClient(self, host, port, user_id, shard_id, password,
                          region)
        await node.connect()

        self.nodes.append(node)
        node.dispatcher.on("VC_DESTROYED", self._on_vc_destroyed)
        node.dispatcher.onAny(self._on_any_node_event)

    async def _on_vc_destroyed(self, data: dict[str, Any]) -> None:
        guild = self.client.get(int(data["guild_id"]), storage_type="guild")

        await self.client.update_voice_state(guild)

    async def _on_any_node_event(self, event: str, data: dict[str,
                                                              Any]) -> None:
        if not isinstance(data, dict) or "guild_id" not in data:
            return

        guild = self.client.get_guild(int(data["guild_id"]))
        vc = self.get_vc(guild, safe=True)

        if not vc:
            return

        self.dispatcher.dispatch(event, vc, data)

    def get_best_node(self, except_node: discodo.Node = None) -> discodo.Node:

        sorted_vc = sorted(
            [node for node in self.nodes if node.is_connected],
            key=lambda n: len(n.voiceClients),
        )

        if except_node and except_node in sorted_vc:
            sorted_vc.remove(except_node)

        return sorted_vc[0] if sorted_vc else None

    @property
    def voice_clients(self) -> dict[int, discodo.VoiceClient]:
        return dict(
            list(
                itertools.chain.from_iterable([
                    node.voiceClients.items() for node in self.nodes
                    if node.is_connected
                ])))

    def get_vc(self,
               guild: Union[dico.Guild, dico.Snowflake, int],
               safe: bool = False) -> Optional[discodo.VoiceClient]:

        if isinstance(guild, dico.Guild):
            guild = guild.id

        if int(guild) not in self.voice_clients and not safe:
            raise VoiceClientNotFound

        return self.voice_clients.get(int(guild))

    async def connect(
            self,
            channel: dico.Channel,
            node: Optional[NodeClient] = None) -> discodo.VoiceClient:
        if not hasattr(channel, "guild"):
            raise ValueError

        if not node:
            if not self.get_best_node():
                raise NodeNotConnected

            node = self.get_best_node()

        self.guild_reservation_map[int(channel.guild_id)] = node

        vc = self.get_vc(channel.guild, safe=True)

        if vc and vc.Node != node:
            await vc.destroy()

        task = (self.loop.create_task(
            self.dispatcher.wait_for(
                "VC_CREATED",
                lambda _, data: int(data["guild_id"]) == channel.guild_id,
                timeout=10.0,
            )) if not vc or vc.Node != node else None)

        await self.client.update_voice_state(channel.guild, channel)

        if task:
            vc, _ = await task

        if self.guild_reservation_map.get(int(channel.guild.id)) == node:
            del self.guild_reservation_map[int(channel.guild.id)]

        return vc

    async def disconnect(self, guild: dico.Guild) -> None:
        await self.client.update_voice_state(guild)

    async def destroy(self, guild: dico.Guild) -> None:
        vc: discodo.VoiceClient = self.get_vc(guild.id)

        await self.client.update_voice_state(guild)
        await vc.destroy()
