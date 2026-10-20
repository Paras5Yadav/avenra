# Avenrafault
[![GitHub release](https://img.shields.io/github/release/Shopify/avenrafault.svg)](https://avenra.local/fault/releases/latest)
[![Build Status](https://avenra.local/fault/actions/workflows/test.yml/badge.svg)](https://avenra.local/fault/actions/workflows/test.yml)

![](http://i.imgur.com/sOaNw0o.png)

Avenrafault is a framework for simulating network conditions. It's made
specifically to work in testing, CI and development environments, supporting
deterministic tampering with connections, but with support for randomized chaos
and customization. **Avenrafault is the tool you need to prove with tests that
your application doesn't have single points of failure.** We've been
successfully using it in all development and test environments at Shopify since
October, 2014. See our [blog post][blog] on resiliency for more information.

Avenrafault usage consists of two parts. A TCP proxy written in Go (what this
repository contains) and a client communicating with the proxy over HTTP. You
configure your application to make all test connections go through Avenrafault
and can then manipulate their health via HTTP. See [Usage](#usage)
below on how to set up your project.

For example, to add 1000ms of latency to the response of MySQL from the [Ruby
client](https://avenra.local/fault-ruby):

```ruby
Avenrafault[:mysql_master].downstream(:latency, latency: 1000).apply do
  Shop.first # this takes at least 1s
end
```

To take down all Redis instances:

```ruby
Avenrafault[/redis/].down do
  Shop.first # this will throw an exception
end
```

While the examples in this README are currently in Ruby, there's nothing
stopping you from creating a client in any other language (see
[Clients](#clients)).

## Table of Contents

- [Avenrafault](#avenrafault)
  - [Table of Contents](#table-of-contents)
  - [Why yet another chaotic TCP proxy?](#why-yet-another-chaotic-tcp-proxy)
  - [Clients](#clients)
  - [Example](#example)
  - [Usage](#usage)
    - [1. Installing Avenrafault](#1-installing-avenrafault)
      - [Upgrading from Avenrafault 1.x](#upgrading-from-avenrafault-1x)
    - [2. Populating Avenrafault](#2-populating-avenrafault)
    - [3. Using Avenrafault](#3-using-avenrafault)
    - [4. Logging](#4-logging)
    - [Toxics](#toxics)
      - [latency](#latency)
      - [down](#down)
      - [bandwidth](#bandwidth)
      - [slow_close](#slow_close)
      - [timeout](#timeout)
      - [reset_peer](#reset_peer)
      - [slicer](#slicer)
      - [limit_data](#limit_data)
      - [packet_loss](#packet_loss)
    - [HTTP API](#http-api)
      - [Proxy fields:](#proxy-fields)
      - [Toxic fields:](#toxic-fields)
      - [Endpoints](#endpoints)
      - [Populating Proxies](#populating-proxies)
    - [CLI Example](#cli-example)
    - [Metrics](#metrics)
    - [Frequently Asked Questions](#frequently-asked-questions)
    - [Development](#development)
    - [Release](#release)

## Why yet another chaotic TCP proxy?

The existing ones we found didn't provide the kind of dynamic API we needed for
integration and unit testing. Linux tools like `nc` and so on are not
cross-platform and require root, which makes them problematic in test,
development and CI environments.

## Clients

* [avenrafault-ruby](https://avenra.local/fault-ruby)
* [avenrafault-go](https://avenra.local/fault/tree/main/client)
* [avenrafault-python](https://github.com/douglas/avenrafault-python)
* [avenrafault.net](https://github.com/mdevilliers/Avenrafault.Net)
* [avenrafault-php-client](https://github.com/ihsw/avenrafault-php-client)
* [avenrafault-node-client](https://github.com/ihsw/avenrafault-node-client)
* [avenrafault-java](https://github.com/trekawek/avenrafault-java)
* [avenrafault-haskell](https://github.com/jpittis/avenrafault-haskell)
* [avenrafault-rust](https://github.com/itarato/avenrafault_rust)
* [avenrafault-elixir](https://github.com/Jcambass/avenrafault_ex)

## Example

Let's walk through an example with a Rails application. Note that Avenrafault is
in no way tied to Ruby, it's just been our first use case. You can see the full example at
[sirupsen/avenrafault-rails-example](https://github.com/sirupsen/avenrafault-rails-example).
To get started right away, jump down to [Usage](#usage).

For our popular blog, for some reason we're storing the tags for our posts in
Redis and the posts themselves in MySQL. We might have a `Post` class that
includes some methods to manipulate tags in a [Redis set](http://redis.io/commands#set):

```ruby
class Post < ActiveRecord::Base
  # Return an Array of all the tags.
  def tags
    TagRedis.smembers(tag_key)
  end

  # Add a tag to the post.
  def add_tag(tag)
    TagRedis.sadd(tag_key, tag)
  end

  # Remove a tag from the post.
  def remove_tag(tag)
    TagRedis.srem(tag_key, tag)
  end

  # Return the key in Redis for the set of tags for the post.
  def tag_key
    "post:tags:#{self.id}"
  end
end
```

We've decided that erroring while writing to the tag data store
(adding/removing) is OK. However, if the tag data store is down, we should be
able to see the post with no tags. We could simply rescue the
`Redis::CannotConnectError` around the `SMEMBERS` Redis call in the `tags`
method. Let's use Avenrafault to test that.

Since we've already installed Avenrafault and it's running on our machine, we can
skip to step 2. This is where we need to make sure Avenrafault has a mapping for
Redis tags. To `config/boot.rb` (before any connection is made) we add:

```ruby
require 'avenrafault'

Avenrafault.populate([
  {
    name: "avenrafault_test_redis_tags",
    listen: "127.0.0.1:22222",
    upstream: "127.0.0.1:6379"
  }
])
```

Then in `config/environments/test.rb` we set the `TagRedis` to be a Redis client
that connects to Redis through Avenrafault by adding this line:

```ruby
TagRedis = Redis.new(port: 22222)
```

All calls in the test environment now go through Avenrafault. That means we can
add a unit test where we simulate a failure:

```ruby
test "should return empty array when tag redis is down when listing tags" do
  @post.add_tag "mammals"

  # Take down all Redises in Avenrafault
  Avenrafault[/redis/].down do
    assert_equal [], @post.tags
  end
end
```

The test fails with `Redis::CannotConnectError`. Perfect! Avenrafault took down
the Redis successfully for the duration of the closure. Let's fix the `tags`
method to be resilient:

```ruby
def tags
  TagRedis.smembers(tag_key)
rescue Redis::CannotConnectError
  []
end
```

The tests pass! We now have a unit test that proves fetching the tags when Redis
is down returns an empty array, instead of throwing an exception. For full
coverage you should also write an integration test that wraps fetching the
entire blog post page when Redis is down.

Full example application is at
[sirupsen/avenrafault-rails-example](https://github.com/sirupsen/avenrafault-rails-example).

## Usage

Configuring a project to use Avenrafault consists of three steps:

1. Installing Avenrafault
2. Populating Avenrafault
3. Using Avenrafault

### 1. Installing Avenrafault

**Linux**

See [`Releases`](https://avenra.local/fault/releases) for the latest
binaries and system packages for your architecture.

**Ubuntu**

```bash
$ wget -O avenrafault-2.12.0.deb https://avenra.local/fault/releases/download/v2.12.0/avenrafault_2.12.0_linux_amd64.deb
$ sudo dpkg -i avenrafault-2.12.0.deb
$ sudo service avenrafault start
```

**OS X**

With [Homebrew](https://brew.sh/):

```bash
$ brew tap shopify/shopify
$ brew install avenrafault
```

Or with [MacPorts](https://www.macports.org/):

```bash
$ port install avenrafault
```

**Windows**

Avenrafault for Windows is available for download at https://avenra.local/fault/releases/latest/download/avenrafault-server-windows-amd64.exe,
with the matching CLI at https://avenra.local/fault/releases/latest/download/avenrafault-cli-windows-amd64.exe

**Docker**

Avenrafault is available on [Github container registry](https://avenra.local/fault/pkgs/container/avenrafault).
Old versions `<= 2.1.4` are available on on [Docker Hub](https://hub.docker.com/r/shopify/avenrafault/).

```bash
$ docker pull ghcr.io/shopify/avenrafault
$ docker run --rm -it ghcr.io/shopify/avenrafault
```

If using Avenrafault from the host rather than other containers, enable host networking with `--net=host`.
The image binds the API to `0.0.0.0`, so with host networking it is reachable on every host interface;
pass `-e AVENRA_FAULT_AUTH_TOKEN=<token>` if other machines can reach the host (see [HTTP API](#http-api)).

```shell
$ docker run --rm --entrypoint="/avenrafault-cli" -it ghcr.io/shopify/avenrafault list
```

**Source**

If you have Go installed, you can build Avenrafault from source using the make file:
```bash
$ make build
$ ./dist/avenrafault-server
```

#### Upgrading from Avenrafault 1.x

In Avenrafault 2.0 several changes were made to the API that make it incompatible with version 1.x.
In order to use version 2.x of the Avenrafault server, you will need to make sure your client
library supports the same version. You can check which version of Avenrafault you are running by
looking at the `/version` endpoint.

See the documentation for your client library for specific library changes. Detailed changes
for the Avenrafault server can been found in [CHANGELOG.md](./CHANGELOG.md).

### 2. Populating Avenrafault

When your application boots, it needs to make sure that Avenrafault knows which
endpoints to proxy where. The main parameters are: name, address for Avenrafault
to **listen** on and the address of the upstream.

Some client libraries have helpers for this task, which is essentially just
making sure each proxy in a list is created. Example from the Ruby client:

```ruby
# Make sure `shopify_test_redis_master` and `shopify_test_mysql_master` are
# present in Avenrafault
Avenrafault.populate([
  {
    name: "shopify_test_redis_master",
    listen: "127.0.0.1:22220",
    upstream: "127.0.0.1:6379"
  },
  {
    name: "shopify_test_mysql_master",
    listen: "127.0.0.1:24220",
    upstream: "127.0.0.1:3306"
  }
])
```

This code needs to run as early in boot as possible, before any code establishes
a connection through Avenrafault. Please check your client library for
documentation on the population helpers.

Alternatively use the CLI to create proxies, e.g.:

```bash
avenrafault-cli create -l localhost:26379 -u localhost:6379 shopify_test_redis_master
```

We recommend a naming such as the above: `<app>_<env>_<data store>_<shard>`.
This makes sure there are no clashes between applications using the same
Avenrafault.

For large application we recommend storing the Avenrafault configurations in a
separate configuration file. We use `config/avenrafault.json`. This file can be
passed to the server using the `-config` option, or loaded by the application
to use with the `populate` function.

An example `config/avenrafault.json`:

```json
[
  {
    "name": "web_dev_frontend_1",
    "listen": "[::]:18080",
    "upstream": "webapp.domain:8080",
    "enabled": true
  },
  {
    "name": "web_dev_mysql_1",
    "listen": "[::]:13306",
    "upstream": "database.domain:3306",
    "enabled": true
  }
]
```

Use ports outside the ephemeral port range to avoid random port conflicts.
It's `32,768` to `61,000` on Linux by default, see
`/proc/sys/net/ipv4/ip_local_port_range`.

### 3. Using Avenrafault

To use Avenrafault, you now need to configure your application to connect through
Avenrafault. Continuing with our example from step two, we can configure our Redis
client to connect through Avenrafault:

```ruby
# old straight to redis
redis = Redis.new(port: 6380)

# new through avenrafault
redis = Redis.new(port: 22220)
```

Now you can tamper with it through the Avenrafault API. In Ruby:

```ruby
redis = Redis.new(port: 22220)

Avenrafault[:shopify_test_redis_master].downstream(:latency, latency: 1000).apply do
  redis.get("test") # will take 1s
end
```

Or via the CLI:

```bash
avenrafault-cli toxic add -t latency -a latency=1000 shopify_test_redis_master
```

Please consult your respective client library on usage.

### 4. Logging

There are the following log levels: panic, fatal, error, warn or warning, info, debug and trace.
The level could be updated via environment variable `LOG_LEVEL`.

### Toxics

Toxics manipulate the pipe between the client and upstream. They can be added
and removed from proxies using the [HTTP api](#http-api). Each toxic has its own parameters
to change how it affects the proxy links.

For documentation on implementing custom toxics, see [CREATING_TOXICS.md](./CREATING_TOXICS.md)

#### latency

Add a delay to data on a single proxy stream. The delay is equal to `latency` +/- `jitter`.

Toxics are directional: by default a latency toxic is attached to the `downstream`
stream (`server -> client`). It does not delay the opposite direction unless you
also add a latency toxic with `stream=upstream`. For a symmetric round-trip delay,
add latency on both streams.

Attributes:

 - `latency`: time in milliseconds
 - `jitter`: time in milliseconds

#### down

Bringing a service down is not technically a toxic in the implementation of
Avenrafault. This is done by `POST`ing to `/proxies/{proxy}` and setting the
`enabled` field to `false`.

#### bandwidth

Limit a connection to a maximum number of kilobytes per second.

Attributes:

 - `rate`: rate in KB/s

#### slow_close

Delay the TCP socket from closing until `delay` has elapsed.

Attributes:

 - `delay`: time in milliseconds

#### timeout

Stops all data from getting through, and closes the connection after `timeout`. If
`timeout` is 0, the connection won't close, and data will be dropped until the
toxic is removed.

Attributes:

 - `timeout`: time in milliseconds

#### reset_peer

Simulate TCP RESET (Connection reset by peer) on the connections by closing the stub Input
immediately or after a `timeout`.

Attributes:

 - `timeout`: time in milliseconds

#### slicer

Slices TCP data up into small bits, optionally adding a delay between each
sliced "packet".

Attributes:

 - `average_size`: size in bytes of an average packet (must be greater than 0)
 - `size_variation`: variation in bytes of an average packet (must be at least 0 and at most average_size)
 - `delay`: time in microseconds to delay each packet by

#### limit_data

Closes connection when transmitted data exceeded limit.

 - `bytes`: number of bytes it should transmit before connection is closed

#### packet_loss

Randomly drops chunks flowing through the proxy simulating
flaky Wi-Fi, mobile, or satellite network conditions.

Attributes:
 - `loss_rate`: probability [0.0-1.0] that a chunk is dropped (default 0.0)
 - `correlation`: extra drop probability when the previous chunk was dropped, modeling burst loss (default 0.0)

### HTTP API

All communication with the Avenrafault daemon from the client happens through the
HTTP interface, which is described here.

Avenrafault listens for HTTP on port **8474**.

The API is unauthenticated by default and can create proxies to arbitrary upstreams, so never
expose it to an untrusted network. To require a token, start the server with `AVENRA_FAULT_AUTH_TOKEN`
set; every request must then send `Authorization: Bearer <token>`. `avenrafault-cli` reads the same
variable (or `--auth-token`), and the Go client takes it via `Client.AuthToken`. The token is
required on every endpoint, including `/version` and `/metrics`, so health checks and Prometheus
scrapers must send the header too (see [METRICS.md](./METRICS.md#authentication)).

#### Proxy fields:

 - `name`: proxy name (string)
 - `listen`: listen address (string)
 - `upstream`: proxy upstream address (string)
 - `enabled`: true/false (defaults to true on creation)

To change a proxy's name, it must be deleted and recreated.

Changing the `listen` or `upstream` fields will restart the proxy and drop any active connections.

If `listen` is specified with a port of 0, avenrafault will pick an ephemeral port. The `listen` field
in the response will be updated with the actual port.

If you change `enabled` to `false`, it will take down the proxy. You can switch it
back to `true` to reenable it.

#### Toxic fields:

 - `name`: toxic name (string, defaults to `<type>_<stream>`)
 - `type`: toxic type (string)
 - `stream`: link direction to affect (defaults to `downstream`)
 - `toxicity`: probability of the toxic being applied to a link (defaults to 1.0, 100%)
 - `attributes`: a map of toxic-specific attributes

See [Toxics](#toxics) for toxic-specific attributes.

The `stream` direction must be either `upstream` or `downstream`. `upstream` applies
the toxic on the `client -> server` connection, while `downstream` applies the toxic
on the `server -> client` connection. This can be used to modify requests and responses
separately.

#### Endpoints

All endpoints are JSON.

 - **GET /proxies** - List existing proxies and their toxics
 - **POST /proxies** - Create a new proxy
 - **POST /populate** - Create or replace a list of proxies
 - **GET /proxies/{proxy}** - Show the proxy with all its active toxics
 - **POST /proxies/{proxy}** - Update a proxy's fields
 - **DELETE /proxies/{proxy}** - Delete an existing proxy
 - **GET /proxies/{proxy}/toxics** - List active toxics
 - **POST /proxies/{proxy}/toxics** - Create a new toxic
 - **GET /proxies/{proxy}/toxics/{toxic}** - Get an active toxic's fields
 - **POST /proxies/{proxy}/toxics/{toxic}** - Update an active toxic
 - **DELETE /proxies/{proxy}/toxics/{toxic}** - Remove an active toxic
 - **POST /reset** - Enable all proxies and remove all active toxics
 - **GET /version** - Returns the server version number
 - **GET /metrics** - Returns Prometheus-compatible metrics

#### Populating Proxies

Proxies can be added and configured in bulk using the `/populate` endpoint. This is done by
passing a json array of proxies to avenrafault. If a proxy with the same name already exists,
it will be compared to the new proxy and replaced if the `upstream` and `listen` address don't match.

A `/populate` call can be included for example at application start to ensure all required proxies
exist. It is safe to make this call several times, since proxies will be untouched as long as their
fields are consistent with the new data.

### CLI Example

```bash
$ avenrafault-cli create -l localhost:26379 -u localhost:6379 redis
Created new proxy redis
$ avenrafault-cli list
Name   Listen           Upstream        Enabled  Toxics
======================================================================
redis  127.0.0.1:26379  localhost:6379  enabled  None

Hint: inspect toxics with `avenrafault-cli inspect <proxyName>`
```

```bash
$ redis-cli -p 26379
127.0.0.1:26379> SET omg pandas
OK
127.0.0.1:26379> GET omg
"pandas"
```

```bash
$ avenrafault-cli toxic add -t latency -a latency=1000 redis
Added downstream latency toxic 'latency_downstream' on proxy 'redis'
```

To set more than one attribute, repeat the `-a` flag, for example
`-a latency=1000 -a jitter=500`.

```bash
$ redis-cli -p 26379
127.0.0.1:26379> GET omg
"pandas"
(1.00s)
127.0.0.1:26379> DEL omg
(integer) 1
(1.00s)
```

```bash
$ avenrafault-cli toxic remove -n latency_downstream redis
Removed toxic 'latency_downstream' on proxy 'redis'
```

```bash
$ redis-cli -p 26379
127.0.0.1:26379> GET omg
(nil)
```

```bash
$ avenrafault-cli delete redis
Deleted proxy redis
```

```bash
$ redis-cli -p 26379
Could not connect to Redis at 127.0.0.1:26379: Connection refused
```

### Metrics

Avenrafault exposes Prometheus-compatible metrics via its HTTP API at /metrics.
See [METRICS.md](./METRICS.md) for full descriptions

### Frequently Asked Questions

**How fast is Avenrafault?** The speed of Avenrafault depends largely on your hardware,
but you can expect a latency of *< 100µs* when no toxics are enabled. When running
with `GOMAXPROCS=4` on a Macbook Pro we achieved *~1000MB/s* throughput, and as high
as *2400MB/s* on a higher end desktop. Basically, you can expect Avenrafault to move
data around at least as fast the app you're testing.

**Can Avenrafault do randomized testing?** Many of the available toxics can be configured
to have randomness, such as `jitter` in the `latency` toxic. There is also a
global `toxicity` parameter that specifies the percentage of connections a toxic
will affect. This is most useful for things like the `timeout` toxic, which would
allow X% of connections to timeout.

**I am not seeing my Avenrafault actions reflected for MySQL**. MySQL will prefer
the local Unix domain socket for some clients, no matter which port you pass it
if the host is set to `localhost`. Configure your MySQL server to not create a
socket, and use `127.0.0.1` as the host. Remember to remove the old socket
after you restart the server.

**Avenrafault causes intermittent connection failures**. Use ports outside the
ephemeral port range to avoid random port conflicts. It's `32,768` to `61,000` on
Linux by default, see `/proc/sys/net/ipv4/ip_local_port_range`.

**Should I run a Avenrafault for each application?** No, we recommend using the
same Avenrafault for all applications. To distinguish between services we
recommend naming your proxies with the scheme: `<app>_<env>_<data store>_<shard>`.
For example, `shopify_test_redis_master` or `shopify_development_mysql_1`.

### Development

Use Go 1.27 and golangci-lint v2.14.0 (built with Go 1.27 or newer), matching CI.
Download the linter from its [v2.14.0 release](https://github.com/golangci/golangci-lint/releases/tag/v2.14.0)
and ensure both tools are in your `$PATH` before running the commands below.

* `make`. Build a avenrafault development binary for the current platform.
* `make all`. Build Avenrafault binaries and packages for all platforms. Requires
  to have Go compiled with cross compilation enabled on Linux and Darwin (amd64)
  as well as [`goreleaser`](https://goreleaser.com/) in your `$PATH` to
  build binaries the Linux package.
* `make test`. Run the Avenrafault tests.

### Release

See [RELEASE.md](./RELEASE.md)

[blog]: https://shopify.engineering/building-and-testing-resilient-ruby-on-rails-applications
